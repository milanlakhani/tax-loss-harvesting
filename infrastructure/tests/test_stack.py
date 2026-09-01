from __future__ import annotations

import pytest
from aws_cdk import App
from aws_cdk import aws_ecs as ecs
from aws_cdk.assertions import Match, Template

from stacks.cidr import CidrRestrictionError, destroy_compatible, require_allowed_cidr
from stacks.tlh_stack import APP_SECRET_JSON_KEYS, TaxLossHarvestingStack

CIDR = "198.51.100.10/32"


def _stack(*, environment_name: str = "demo", cidr: str = CIDR) -> TaxLossHarvestingStack:
    app = App()
    return TaxLossHarvestingStack(
        app,
        "TestStack",
        allowed_ipv4_cidr=cidr,
        environment_name=environment_name,
        container_image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/python:3.12-slim"),
    )


def _template(**kwargs) -> Template:
    return Template.from_stack(_stack(**kwargs))


def test_require_allowed_cidr_rejects_blank_and_open_internet():
    with pytest.raises(CidrRestrictionError):
        require_allowed_cidr("")
    with pytest.raises(CidrRestrictionError):
        require_allowed_cidr("0.0.0.0/0")
    with pytest.raises(CidrRestrictionError):
        require_allowed_cidr("::/0")
    assert require_allowed_cidr("198.51.100.10/32") == "198.51.100.10/32"


def test_destroy_compatible_does_not_apply_to_production_names():
    assert destroy_compatible("demo") is True
    assert destroy_compatible("production") is False
    assert destroy_compatible("prod") is False


def test_alb_ingress_is_operator_cidr_not_world():
    template = _template()
    ingress_cidrs = []
    for resource in template.find_resources("AWS::EC2::SecurityGroup").values():
        for rule in resource["Properties"].get("SecurityGroupIngress", []):
            if rule.get("FromPort") == 80:
                ingress_cidrs.append(rule.get("CidrIp"))
    assert CIDR in ingress_cidrs
    assert "0.0.0.0/0" not in ingress_cidrs
    template.has_output("AllowedIpv4Cidr", {"Value": CIDR})


def test_synth_fails_for_open_cidr():
    with pytest.raises(CidrRestrictionError):
        _stack(cidr="0.0.0.0/0")


def test_fargate_assigns_public_ip_in_public_subnets_desired_count_one():
    template = _template()
    template.has_resource_properties(
        "AWS::ECS::Service",
        {
            "DesiredCount": 1,
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "AssignPublicIp": "ENABLED",
                }
            },
        },
    )
    template.resource_count_is("AWS::EC2::NatGateway", 0)


def test_alb_routes_api_mcp_health_to_backend_and_default_to_streamlit():
    template = _template()
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::ListenerRule",
        {
            "Priority": 10,
            "Conditions": Match.array_with(
                [
                    Match.object_like(
                        {
                            "Field": "path-pattern",
                            "PathPatternConfig": {
                                "Values": Match.array_with(["/api/*", "/mcp", "/health"])
                            },
                        }
                    )
                ]
            ),
        },
    )
    groups = template.find_resources("AWS::ElasticLoadBalancingV2::TargetGroup")
    ports = {props["Properties"]["Port"] for props in groups.values()}
    assert ports == {8000, 8501}
    health_paths = {props["Properties"]["HealthCheckPath"] for props in groups.values()}
    assert "/health" in health_paths
    assert "/_stcore/health" in health_paths
    sticky = [
        props["Properties"].get("TargetGroupAttributes", [])
        for props in groups.values()
        if props["Properties"]["Port"] == 8501
    ]
    assert any(
        any(attr.get("Key") == "stickiness.enabled" and attr.get("Value") == "true" for attr in attrs)
        for attrs in sticky
    )


def test_secrets_are_json_keys_not_plaintext_values():
    template = _template()
    serialized = template.to_json()
    blob = str(serialized)
    assert "sk-proj" not in blob
    assert "AKIA" not in blob
    for key in APP_SECRET_JSON_KEYS:
        assert key in blob
    template.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        Match.object_like(
            {
                "ContainerDefinitions": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Name": "backend",
                                "Secrets": Match.array_with(
                                    [
                                        Match.object_like(
                                            {"Name": "OPENAI_API_KEY", "ValueFrom": Match.any_value()}
                                        )
                                    ]
                                ),
                            }
                        )
                    ]
                )
            }
        ),
    )
    streamlit = None
    for resource in template.find_resources("AWS::ECS::TaskDefinition").values():
        for container in resource["Properties"]["ContainerDefinitions"]:
            if container["Name"] == "streamlit":
                streamlit = container
    assert streamlit is not None
    secret_names = {item["Name"] for item in streamlit.get("Secrets", [])}
    assert "OPENAI_API_KEY" not in secret_names
    assert "ALPACA_ACCOUNT_1_SECRET" not in secret_names
    assert "WHATSAPP_ACCESS_TOKEN" not in blob


def test_dynamodb_keys_ttl_and_on_demand_billing():
    template = _template()
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": Match.array_with(
                [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ]
            ),
            "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True},
        },
    )


def test_s3_is_encrypted_and_blocks_public_access():
    template = _template()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": Match.any_value(),
        },
    )


def test_rds_is_isolated_encrypted_single_az():
    template = _template()
    template.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "PubliclyAccessible": False,
            "StorageEncrypted": True,
            "MultiAZ": False,
            "DBInstanceClass": "db.t3.micro",
        },
    )
    template.resource_count_is("AWS::RDS::DBInstance", 1)
    isolated = False
    for resource in template.find_resources("AWS::EC2::Subnet").values():
        if resource["Properties"].get("MapPublicIpOnLaunch") is False:
            isolated = True
    assert isolated


def test_iam_task_policies_are_scoped_to_this_stack():
    template = _template()
    for resource in template.find_resources("AWS::IAM::Policy").values():
        name = resource["Properties"].get("PolicyName", "")
        if "TaskDef" not in str(name) and "TaskRole" not in str(resource):
            continue
        for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            interesting = [
                action
                for action in actions
                if action.startswith("s3:") or action.startswith("dynamodb:") or action.startswith("secretsmanager:")
            ]
            if not interesting:
                continue
            resources = statement.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            assert "*" not in resources, f"wildcard IAM resource for {interesting}"


def test_demo_removal_destroys_and_production_retains():
    demo = _template(environment_name="demo")
    demo.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Delete"})
    demo.has_resource("AWS::DynamoDB::Table", {"DeletionPolicy": "Delete"})
    demo.has_resource("AWS::RDS::DBInstance", {"DeletionPolicy": "Delete"})

    prod = _template(environment_name="production")
    prod.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain"})
    prod.has_resource("AWS::DynamoDB::Table", {"DeletionPolicy": "Retain"})
    prod.has_resource("AWS::RDS::DBInstance", {"DeletionPolicy": "Retain"})


def test_vpc_has_gateway_endpoints_and_log_retention():
    template = _template()
    template.resource_count_is("AWS::EC2::VPCEndpoint", 2)
    template.has_resource_properties(
        "AWS::Logs::LogGroup",
        {"RetentionInDays": 14},
    )


def test_migration_task_uses_alembic_and_backend_secrets():
    template = _template()
    found = False
    for resource in template.find_resources("AWS::ECS::TaskDefinition").values():
        for container in resource["Properties"]["ContainerDefinitions"]:
            if container.get("Command") == ["alembic", "upgrade", "head"]:
                found = True
                names = {item["Name"] for item in container.get("Secrets", [])}
                assert "POSTGRES_PASSWORD" in names
                assert "OPENAI_API_KEY" in names
    assert found
    template.has_output("MigrationTaskDefinitionArn", Match.any_value())
