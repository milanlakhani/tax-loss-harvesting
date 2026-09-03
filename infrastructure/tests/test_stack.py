from __future__ import annotations

import pytest
from aws_cdk import App
from aws_cdk import aws_ecs as ecs
from aws_cdk.assertions import Match, Template

from stacks.cidr import CidrRestrictionError, destroy_compatible, require_allowed_cidr
from stacks.tlh_stack import (
    APP_SECRET_JSON_KEYS,
    AWS_CURRENT_DEMO_ENVIRONMENT,
    ServiceDesiredCountError,
    TaxLossHarvestingStack,
    require_service_desired_count,
)

CIDR = "198.51.100.10/32"


def _stack(
    *,
    environment_name: str = "demo",
    cidr: str = CIDR,
    service_desired_count=None,
) -> TaxLossHarvestingStack:
    app = App()
    kwargs = {}
    if service_desired_count is not None:
        kwargs["service_desired_count"] = service_desired_count
    return TaxLossHarvestingStack(
        app,
        "TestStack",
        allowed_ipv4_cidr=cidr,
        environment_name=environment_name,
        container_image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/python:3.12-slim"),
        **kwargs,
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
    template = _template(service_desired_count=1)
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


def test_alb_routes_api_health_to_backend_and_default_to_streamlit():
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
                                "Values": Match.array_with(["/api/*", "/health"])
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
    for resource in template.find_resources("AWS::ElasticLoadBalancingV2::ListenerRule").values():
        blob = str(resource)
        assert "/mcp" not in blob
    assert 8001 not in {
        rule.get("FromPort")
        for resource in template.find_resources("AWS::EC2::SecurityGroup").values()
        for rule in resource["Properties"].get("SecurityGroupIngress", [])
    }
    for resource in template.find_resources("AWS::EC2::SecurityGroupIngress").values():
        assert resource["Properties"].get("FromPort") != 8001
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
    assert "pk-lf-" not in blob
    assert "sk-lf-" not in blob
    assert "cloud.langfuse.com" not in blob


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


def test_statement_bucket_has_no_s3_auto_delete_custom_resource():
    demo = _template(environment_name="demo")
    prod = _template(environment_name="production")
    for template in (demo, prod):
        blob = str(template.to_json())
        assert "S3AutoDeleteObjects" not in blob
        assert "Custom::S3AutoDeleteObjects" not in blob
        template.resource_count_is("AWS::Lambda::Function", 0)
        template.has_output("StatementBucketName", Match.any_value())
    demo.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Delete"})
    prod.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain"})


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
                assert "LANGFUSE_SECRET_KEY" not in names
                assert "LANGFUSE_PUBLIC_KEY" not in names
    assert found
    template.has_output("MigrationTaskDefinitionArn", Match.any_value())


def test_mcp_sidecar_is_task_local_on_port_8001():
    from stacks.tlh_stack import AWS_MCP_SERVER_URL, MCP_PORT

    template = _template()
    demo_task = None
    for resource in template.find_resources("AWS::ECS::TaskDefinition").values():
        names = {container["Name"] for container in resource["Properties"]["ContainerDefinitions"]}
        if {"backend", "streamlit", "mcp"} <= names:
            demo_task = resource
            break
    assert demo_task is not None
    containers = {row["Name"]: row for row in demo_task["Properties"]["ContainerDefinitions"]}
    mcp = containers["mcp"]
    backend = containers["backend"]
    streamlit = containers["streamlit"]
    assert mcp.get("Essential") is not False
    assert any(mapping.get("ContainerPort") == MCP_PORT for mapping in mcp.get("PortMappings", []))
    assert mcp.get("HealthCheck")
    env = {item["Name"]: item["Value"] for item in backend.get("Environment", [])}
    assert env.get("MCP_SERVER_URL") == AWS_MCP_SERVER_URL
    mcp_env = {item["Name"]: item["Value"] for item in mcp.get("Environment", [])}
    assert "MCP_SERVER_URL" not in mcp_env
    mcp_secrets = {item["Name"] for item in mcp.get("Secrets", [])}
    assert "OPENAI_API_KEY" not in mcp_secrets
    assert "DEMO_SESSION_SIGNING_SECRET" not in mcp_secrets
    assert "LANGFUSE_PUBLIC_KEY" not in mcp_secrets
    assert "LANGFUSE_SECRET_KEY" not in mcp_secrets
    assert "ALPACA_ACCOUNT_1_KEY" in mcp_secrets
    assert "POSTGRES_PASSWORD" in mcp_secrets
    streamlit_secrets = {item["Name"] for item in streamlit.get("Secrets", [])}
    assert "ALPACA_ACCOUNT_1_SECRET" not in streamlit_secrets
    assert "LANGFUSE_PUBLIC_KEY" not in streamlit_secrets
    assert "LANGFUSE_SECRET_KEY" not in streamlit_secrets
    depends = backend.get("DependsOn") or []
    assert any(
        item.get("ContainerName") == "mcp" and item.get("Condition") == "HEALTHY" for item in depends
    )
    groups = template.find_resources("AWS::ElasticLoadBalancingV2::TargetGroup")
    assert 8001 not in {props["Properties"]["Port"] for props in groups.values()}
    template.resource_count_is("AWS::ECS::Service", 1)
    template.resource_count_is("AWS::ElasticLoadBalancingV2::LoadBalancer", 1)
    template.resource_count_is("AWS::EC2::NatGateway", 0)
    template.has_output("McpInternalUrl", {"Value": AWS_MCP_SERVER_URL})


def test_langfuse_secrets_are_injected_only_on_backend():
    from stacks.tlh_stack import LANGFUSE_BACKEND_SECRET_KEYS

    template = _template()
    demo_task = None
    migrate = None
    for resource in template.find_resources("AWS::ECS::TaskDefinition").values():
        names = {container["Name"] for container in resource["Properties"]["ContainerDefinitions"]}
        if {"backend", "streamlit", "mcp"} <= names:
            demo_task = resource
        for container in resource["Properties"]["ContainerDefinitions"]:
            if container.get("Command") == ["alembic", "upgrade", "head"]:
                migrate = container
    assert demo_task is not None
    assert migrate is not None
    containers = {row["Name"]: row for row in demo_task["Properties"]["ContainerDefinitions"]}
    backend_secrets = {item["Name"] for item in containers["backend"].get("Secrets", [])}
    mcp_secrets = {item["Name"] for item in containers["mcp"].get("Secrets", [])}
    streamlit_secrets = {item["Name"] for item in containers["streamlit"].get("Secrets", [])}
    migrate_secrets = {item["Name"] for item in migrate.get("Secrets", [])}
    for key in LANGFUSE_BACKEND_SECRET_KEYS:
        assert key in backend_secrets
        assert key not in mcp_secrets
        assert key not in streamlit_secrets
        assert key not in migrate_secrets
    backend_env = {item["Name"]: item["Value"] for item in containers["backend"].get("Environment", [])}
    assert "LANGFUSE_PUBLIC_KEY" not in backend_env
    assert "LANGFUSE_SECRET_KEY" not in backend_env
    assert "LANGFUSE_BASE_URL" not in backend_env
    assert "LANGFUSE_ENABLED" not in backend_env
    backend_secret_refs = {item["Name"]: item.get("ValueFrom") for item in containers["backend"].get("Secrets", [])}
    for key in LANGFUSE_BACKEND_SECRET_KEYS:
        assert backend_secret_refs[key]
    mcp_env = {item["Name"]: item["Value"] for item in containers["mcp"].get("Environment", [])}
    streamlit_env = {item["Name"]: item["Value"] for item in containers["streamlit"].get("Environment", [])}
    assert mcp_env.get("LANGFUSE_ENABLED") == "false"
    assert streamlit_env.get("LANGFUSE_ENABLED") == "false"
    assert "LANGFUSE_BASE_URL" not in mcp_env
    assert "LANGFUSE_BASE_URL" not in streamlit_env
    outputs = template.to_json().get("Outputs", {})
    assert "Langfuse" not in str(outputs)
    blob = str(template.to_json())
    assert "pk-lf-" not in blob
    assert "sk-lf-" not in blob
    assert "cloud.langfuse.com" not in blob
    assert "cloud.langfuse.eu" not in blob
    egress_ports = {
        (rule.get("FromPort"), rule.get("ToPort"))
        for resource in template.find_resources("AWS::EC2::SecurityGroup").values()
        for rule in resource["Properties"].get("SecurityGroupEgress", [])
    }
    assert (443, 443) in egress_ports
    ingress_ports = {
        rule.get("FromPort")
        for resource in template.find_resources("AWS::EC2::SecurityGroup").values()
        for rule in resource["Properties"].get("SecurityGroupIngress", [])
    }
    assert 443 not in ingress_ports
    import json
    from pathlib import Path

    cdk_json = json.loads((Path(__file__).resolve().parents[1] / "cdk.json").read_text(encoding="utf-8"))
    assert "langfuse" not in json.dumps(cdk_json).lower()
    assert cdk_json["context"]["service_desired_count"] == 0


def _service_circuit_breaker(desired_count: int) -> dict:
    return {
        "DesiredCount": desired_count,
        "DeploymentConfiguration": {
            "DeploymentCircuitBreaker": {
                "Enable": True,
                "Rollback": True,
            }
        },
    }


@pytest.mark.parametrize("count", [0, 1, "0", "1"])
def test_service_desired_count_zero_and_one_synthesize(count):
    expected = int(count)
    template = _template(service_desired_count=count)
    template.has_resource_properties("AWS::ECS::Service", _service_circuit_breaker(expected))
    template.has_output("ServiceDesiredCount", {"Value": str(expected)})


def test_service_desired_count_defaults_to_zero():
    assert require_service_desired_count(None) == 0
    assert require_service_desired_count("") == 0
    template = _template()
    template.has_resource_properties("AWS::ECS::Service", _service_circuit_breaker(0))
    template.has_output("ServiceDesiredCount", {"Value": "0"})


@pytest.mark.parametrize("value", [2, -1, "2", "true", "1.0", 1.5, True, False, 3])
def test_service_desired_count_rejects_other_values(value):
    with pytest.raises(ServiceDesiredCountError):
        _stack(service_desired_count=value)
    with pytest.raises(ServiceDesiredCountError):
        require_service_desired_count(value)


def test_app_context_passes_service_desired_count_into_the_stack():
    app = App(context={"service_desired_count": "1"})
    stack = TaxLossHarvestingStack(
        app,
        "ContextStack",
        allowed_ipv4_cidr=CIDR,
        environment_name="demo",
        container_image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/python:3.12-slim"),
        service_desired_count=require_service_desired_count(app.node.try_get_context("service_desired_count")),
    )
    template = Template.from_stack(stack)
    template.has_resource_properties("AWS::ECS::Service", _service_circuit_breaker(1))
    template.has_output("ServiceDesiredCount", {"Value": "1"})


def _named_containers(template: Template) -> dict[str, dict]:
    found: dict[str, dict] = {}
    migrate = None
    for resource in template.find_resources("AWS::ECS::TaskDefinition").values():
        for container in resource["Properties"]["ContainerDefinitions"]:
            found[container["Name"]] = container
            if container.get("Command") == ["alembic", "upgrade", "head"]:
                migrate = container
    found["migrate"] = migrate
    return found


def _env(container: dict) -> dict[str, str]:
    return {item["Name"]: item["Value"] for item in container.get("Environment", [])}


def _secret_names(container: dict) -> set[str]:
    return {item["Name"] for item in container.get("Secrets", [])}


def test_current_demo_mode_is_non_secret_ecs_environment_on_backend_mcp_and_migration():
    containers = _named_containers(_template())
    for name in ("backend", "mcp", "migrate"):
        env = _env(containers[name])
        secrets = _secret_names(containers[name])
        for key, value in AWS_CURRENT_DEMO_ENVIRONMENT.items():
            assert env.get(key) == value
            assert key not in secrets
            assert key not in APP_SECRET_JSON_KEYS
    streamlit_env = _env(containers["streamlit"])
    assert "DEMO_MODE" not in streamlit_env
    assert "DEMO_AS_OF_DATE" not in streamlit_env
    assert "DEMO_STATEMENT_MAX_AGE_DAYS" not in streamlit_env


def test_paper_order_flags_remain_hardcoded():
    containers = _named_containers(_template())
    backend = _env(containers["backend"])
    mcp = _env(containers["mcp"])
    migrate = _env(containers["migrate"])
    assert backend["ENABLE_PAPER_ORDERS"] == "true"
    assert backend["ALPACA_PAPER"] == "true"
    assert migrate["ENABLE_PAPER_ORDERS"] == "true"
    assert migrate["ALPACA_PAPER"] == "true"
    assert mcp["ENABLE_PAPER_ORDERS"] == "false"
    assert mcp["ALPACA_PAPER"] == "true"


