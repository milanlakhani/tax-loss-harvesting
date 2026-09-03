from __future__ import annotations

import json
import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Environment,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from stacks.cidr import destroy_compatible, require_allowed_cidr

REPO_ROOT = Path(__file__).resolve().parents[2]

APP_SECRET_JSON_KEYS = (
    "OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "COINGECKO_API_KEY",
    "ALPACA_ACCOUNT_1_KEY",
    "ALPACA_ACCOUNT_1_SECRET",
    "ALPACA_ACCOUNT_2_KEY",
    "ALPACA_ACCOUNT_2_SECRET",
    "DEMO_SESSION_SIGNING_SECRET",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_TRACING_ENVIRONMENT",
    "LANGFUSE_ENABLED",
)

LANGFUSE_BACKEND_SECRET_KEYS = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_TRACING_ENVIRONMENT",
    "LANGFUSE_ENABLED",
)

BACKEND_PORT = 8000
MCP_PORT = 8001
STREAMLIT_PORT = 8501
AWS_MCP_SERVER_URL = "http://127.0.0.1:8001/mcp"
DEFAULT_SERVICE_DESIRED_COUNT = 0
ALLOWED_SERVICE_DESIRED_COUNTS = frozenset({0, 1})
AWS_CURRENT_DEMO_ENVIRONMENT = {
    "DEMO_MODE": "current",
    "DEMO_AS_OF_DATE": "today",
    "DEMO_STATEMENT_MAX_AGE_DAYS": "20",
}


class ServiceDesiredCountError(ValueError):
    """Raised when CDK synthesis would use a Fargate desired count other than 0 or 1."""


def require_service_desired_count(value: object = None) -> int:
    """Accept only integer 0 or 1. Missing values default to 0 so the first deploy stays idle."""
    if value is None or value == "":
        return DEFAULT_SERVICE_DESIRED_COUNT
    if isinstance(value, bool):
        raise ServiceDesiredCountError("service_desired_count must be the integer 0 or 1.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped in {"0", "1"}:
            parsed = int(stripped)
        else:
            raise ServiceDesiredCountError(
                f"service_desired_count must be the integer 0 or 1 (got {value!r})."
            )
    else:
        raise ServiceDesiredCountError(
            f"service_desired_count must be the integer 0 or 1 (got {value!r})."
        )
    if parsed not in ALLOWED_SERVICE_DESIRED_COUNTS:
        raise ServiceDesiredCountError(
            f"service_desired_count must be the integer 0 or 1 (got {parsed!r})."
        )
    return parsed


class TaxLossHarvestingStack(Stack):
    """Minimal demo AWS topology. Public task IPs are a cost-saving demo choice, not production."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        allowed_ipv4_cidr: str,
        environment_name: str = "demo",
        container_image: ecs.ContainerImage | None = None,
        service_desired_count: object = DEFAULT_SERVICE_DESIRED_COUNT,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.allowed_ipv4_cidr = require_allowed_cidr(allowed_ipv4_cidr)
        self.environment_name = environment_name.strip() or "demo"
        self.service_desired_count = require_service_desired_count(service_desired_count)
        demo_destroy = destroy_compatible(self.environment_name)
        removal = RemovalPolicy.DESTROY if demo_destroy else RemovalPolicy.RETAIN
        log_retention = logs.RetentionDays.TWO_WEEKS

        Tags.of(self).add("Project", "tax-loss-harvesting")
        Tags.of(self).add("Environment", self.environment_name)
        Tags.of(self).add("AllowedIpv4Cidr", self.allowed_ipv4_cidr)

        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            enable_dns_hostnames=True,
            enable_dns_support=True,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        vpc.add_gateway_endpoint("DynamoDbEndpoint", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)

        alb_sg = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            description="ALB ingress from the operator CIDR only",
            allow_all_outbound=False,
        )
        task_sg = ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=vpc,
            description="Fargate: ALB inbound; PostgreSQL, HTTPS, and DNS outbound",
            allow_all_outbound=False,
        )
        rds_sg = ec2.SecurityGroup(
            self,
            "RdsSecurityGroup",
            vpc=vpc,
            description="RDS PostgreSQL inbound from the Fargate task security group",
            allow_all_outbound=False,
        )

        alb_sg.add_ingress_rule(
            ec2.Peer.ipv4(self.allowed_ipv4_cidr),
            ec2.Port.tcp(80),
            "Operator CIDR to ALB HTTP",
        )
        task_sg.connections.allow_from(alb_sg, ec2.Port.tcp(BACKEND_PORT), "ALB to FastAPI")
        task_sg.connections.allow_from(alb_sg, ec2.Port.tcp(STREAMLIT_PORT), "ALB to Streamlit")
        rds_sg.connections.allow_from(task_sg, ec2.Port.tcp(5432), "Fargate to PostgreSQL")
        task_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS for OpenAI, Alpaca, Alpha Vantage, CoinGecko, Frankfurter, AWS APIs, ECR",
        )
        task_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.udp(53), "DNS UDP (VPC resolver)")
        task_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(53), "DNS TCP (VPC resolver)")

        statements = s3.Bucket(
            self,
            "StatementBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            public_read_access=False,
            removal_policy=removal,
        )

        windows = dynamodb.Table(
            self,
            "RollingWindowTable",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=removal,
            point_in_time_recovery=False,
        )

        app_secret = secretsmanager.Secret(
            self,
            "AppSecret",
            description=(
                "Application credentials JSON. Operator must put non-blank values after deploy. "
                "Values are never placed in CDK, context, or CloudFormation outputs."
            ),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({key: "" for key in APP_SECRET_JSON_KEYS}),
                generate_string_key="generated_placeholder",
                exclude_characters=" %+~`#$&*()|[]{}:;<>?!'/@\"\\",
            ),
            removal_policy=removal,
        )

        database = rds.DatabaseInstance(
            self,
            "Postgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.of("16.15", "16"),
            ),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[rds_sg],
            credentials=rds.Credentials.from_generated_secret("finance"),
            database_name="finance",
            allocated_storage=20,
            multi_az=False,
            publicly_accessible=False,
            storage_encrypted=True,
            deletion_protection=not demo_destroy,
            removal_policy=removal,
            backup_retention=Duration.days(1 if demo_destroy else 7),
            delete_automated_backups=demo_destroy,
        )
        rds_secret = database.secret
        if rds_secret is None:
            raise RuntimeError("RDS-generated secret is required")

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights=False)

        image = container_image
        if image is None:
            asset = ecr_assets.DockerImageAsset(
                self,
                "AppImage",
                directory=str(REPO_ROOT),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_AMD64,
            )
            image = ecs.ContainerImage.from_docker_image_asset(asset)

        task = ecs.FargateTaskDefinition(
            self,
            "DemoTask",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        backend_logs = logs.LogGroup(
            self,
            "BackendLogGroup",
            retention=log_retention,
            removal_policy=removal,
        )
        mcp_logs = logs.LogGroup(
            self,
            "McpLogGroup",
            retention=log_retention,
            removal_policy=removal,
        )
        ui_logs = logs.LogGroup(
            self,
            "StreamlitLogGroup",
            retention=log_retention,
            removal_policy=removal,
        )
        migration_logs = logs.LogGroup(
            self,
            "MigrationLogGroup",
            retention=log_retention,
            removal_policy=removal,
        )

        backend_env = {
            "APP_ENV": "aws",
            "AWS_REGION": Stack.of(self).region,
            "AWS_DEFAULT_REGION": Stack.of(self).region,
            "STATEMENTS_BUCKET": statements.bucket_name,
            "DYNAMODB_TABLE": windows.table_name,
            "APP_SECRET_ARN": app_secret.secret_arn,
            "POSTGRES_HOST": database.instance_endpoint.hostname,
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "finance",
            "USE_LIVE_PROVIDERS": "true",
            "ENABLE_PAPER_ORDERS": "true",
            "ALPACA_PAPER": "true",
            "ENABLE_LLM_ORCHESTRATOR": "true",
            "ALPACA_ACCOUNT_1_NAME": "conservative-demo",
            "ALPACA_ACCOUNT_2_NAME": "growth-demo",
            "COINGECKO_API_PLAN": "demo",
            "PYTHONUNBUFFERED": "1",
            "MCP_SERVER_URL": AWS_MCP_SERVER_URL,
            **AWS_CURRENT_DEMO_ENVIRONMENT,
        }
        mcp_env = {
            "APP_ENV": "aws",
            "AWS_REGION": Stack.of(self).region,
            "AWS_DEFAULT_REGION": Stack.of(self).region,
            "STATEMENTS_BUCKET": statements.bucket_name,
            "DYNAMODB_TABLE": windows.table_name,
            "POSTGRES_HOST": database.instance_endpoint.hostname,
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "finance",
            "USE_LIVE_PROVIDERS": "true",
            "ENABLE_PAPER_ORDERS": "false",
            "ALPACA_PAPER": "true",
            "ENABLE_LLM_ORCHESTRATOR": "false",
            "ALPACA_ACCOUNT_1_NAME": "conservative-demo",
            "ALPACA_ACCOUNT_2_NAME": "growth-demo",
            "COINGECKO_API_PLAN": "demo",
            "PYTHONUNBUFFERED": "1",
            "LANGFUSE_ENABLED": "false",
            **AWS_CURRENT_DEMO_ENVIRONMENT,
        }
        backend_secrets = {
            "OPENAI_API_KEY": ecs.Secret.from_secrets_manager(app_secret, "OPENAI_API_KEY"),
            "ALPHA_VANTAGE_API_KEY": ecs.Secret.from_secrets_manager(app_secret, "ALPHA_VANTAGE_API_KEY"),
            "COINGECKO_API_KEY": ecs.Secret.from_secrets_manager(app_secret, "COINGECKO_API_KEY"),
            "ALPACA_ACCOUNT_1_KEY": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_1_KEY"),
            "ALPACA_ACCOUNT_1_SECRET": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_1_SECRET"),
            "ALPACA_ACCOUNT_2_KEY": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_2_KEY"),
            "ALPACA_ACCOUNT_2_SECRET": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_2_SECRET"),
            "DEMO_SESSION_SIGNING_SECRET": ecs.Secret.from_secrets_manager(
                app_secret, "DEMO_SESSION_SIGNING_SECRET"
            ),
            "POSTGRES_USER": ecs.Secret.from_secrets_manager(rds_secret, "username"),
            "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(rds_secret, "password"),
        }
        langfuse_backend_secrets = {
            key: ecs.Secret.from_secrets_manager(app_secret, key) for key in LANGFUSE_BACKEND_SECRET_KEYS
        }
        mcp_secrets = {
            "ALPHA_VANTAGE_API_KEY": ecs.Secret.from_secrets_manager(app_secret, "ALPHA_VANTAGE_API_KEY"),
            "COINGECKO_API_KEY": ecs.Secret.from_secrets_manager(app_secret, "COINGECKO_API_KEY"),
            "ALPACA_ACCOUNT_1_KEY": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_1_KEY"),
            "ALPACA_ACCOUNT_1_SECRET": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_1_SECRET"),
            "ALPACA_ACCOUNT_2_KEY": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_2_KEY"),
            "ALPACA_ACCOUNT_2_SECRET": ecs.Secret.from_secrets_manager(app_secret, "ALPACA_ACCOUNT_2_SECRET"),
            "POSTGRES_USER": ecs.Secret.from_secrets_manager(rds_secret, "username"),
            "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(rds_secret, "password"),
        }

        mcp = task.add_container(
            "mcp",
            image=image,
            essential=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="mcp", log_group=mcp_logs),
            environment=mcp_env,
            secrets=mcp_secrets,
            command=[
                "uvicorn",
                "app.mcp.asgi:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(MCP_PORT),
            ],
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    f"python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{MCP_PORT}/health')\"",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        mcp.add_port_mappings(ecs.PortMapping(container_port=MCP_PORT, protocol=ecs.Protocol.TCP))

        backend = task.add_container(
            "backend",
            image=image,
            essential=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="backend", log_group=backend_logs),
            environment=backend_env,
            secrets={**backend_secrets, **langfuse_backend_secrets},
            command=[
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(BACKEND_PORT),
            ],
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", f"python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{BACKEND_PORT}/health')\""],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        backend.add_port_mappings(ecs.PortMapping(container_port=BACKEND_PORT, protocol=ecs.Protocol.TCP))
        backend.add_container_dependencies(
            ecs.ContainerDependency(
                container=mcp,
                condition=ecs.ContainerDependencyCondition.HEALTHY,
            )
        )

        streamlit = task.add_container(
            "streamlit",
            image=image,
            essential=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="streamlit", log_group=ui_logs),
            environment={
                "BACKEND_URL": f"http://127.0.0.1:{BACKEND_PORT}",
                "PYTHONPATH": "/app",
                "STREAMLIT_SERVER_HEADLESS": "true",
                "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
                "LANGFUSE_ENABLED": "false",
            },
            command=[
                "streamlit",
                "run",
                "app/ui/streamlit_app.py",
                "--server.address=0.0.0.0",
                "--server.port=" + str(STREAMLIT_PORT),
                "--server.headless=true",
                "--server.enableCORS=false",
                "--server.enableXsrfProtection=true",
            ],
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    f"python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{STREAMLIT_PORT}/_stcore/health')\"",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(90),
            ),
        )
        streamlit.add_port_mappings(ecs.PortMapping(container_port=STREAMLIT_PORT, protocol=ecs.Protocol.TCP))
        streamlit.add_container_dependencies(
            ecs.ContainerDependency(
                container=backend,
                condition=ecs.ContainerDependencyCondition.HEALTHY,
            )
        )

        statements.grant_read_write(task.task_role)
        windows.grant_read_write_data(task.task_role)
        app_secret.grant_read(task.task_role)
        app_secret.grant_read(task.obtain_execution_role())
        rds_secret.grant_read(task.task_role)
        rds_secret.grant_read(task.obtain_execution_role())

        migration_task = ecs.FargateTaskDefinition(
            self,
            "MigrationTask",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task.task_role,
            execution_role=task.obtain_execution_role(),
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        migration_task.add_container(
            "migrate",
            image=image,
            essential=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="migrate", log_group=migration_logs),
            environment=backend_env,
            secrets=backend_secrets,
            command=["alembic", "upgrade", "head"],
        )

        service = ecs.FargateService(
            self,
            "DemoService",
            cluster=cluster,
            task_definition=task,
            desired_count=self.service_desired_count,
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[task_sg],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=0,
            max_healthy_percent=100,
        )

        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            idle_timeout=Duration.seconds(400),
            deletion_protection=not demo_destroy,
        )
        listener = alb.add_listener(
            "Http",
            port=80,
            open=False,
            protocol=elbv2.ApplicationProtocol.HTTP,
        )

        backend_target = elbv2.ApplicationTargetGroup(
            self,
            "BackendTarget",
            vpc=vpc,
            port=BACKEND_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=Duration.seconds(10),
        )
        backend_target.add_target(
            service.load_balancer_target(container_name="backend", container_port=BACKEND_PORT)
        )

        ui_target = elbv2.ApplicationTargetGroup(
            self,
            "StreamlitTarget",
            vpc=vpc,
            port=STREAMLIT_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/_stcore/health",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            stickiness_cookie_duration=Duration.hours(1),
            deregistration_delay=Duration.seconds(10),
        )
        ui_target.add_target(
            service.load_balancer_target(container_name="streamlit", container_port=STREAMLIT_PORT)
        )

        listener.add_target_groups(
            "BackendRoutes",
            target_groups=[backend_target],
            priority=10,
            conditions=[
                elbv2.ListenerCondition.path_patterns(
                    ["/api/*", "/health", "/health/*"]
                )
            ],
        )
        listener.add_target_groups("DefaultUi", target_groups=[ui_target])

        public_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC).subnet_ids
        isolated_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED).subnet_ids

        CfnOutput(self, "LoadBalancerDns", value=alb.load_balancer_dns_name)
        CfnOutput(self, "AllowedIpv4Cidr", value=self.allowed_ipv4_cidr)
        CfnOutput(self, "EnvironmentName", value=self.environment_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "ServiceDesiredCount", value=str(self.service_desired_count))
        CfnOutput(self, "StatementBucketName", value=statements.bucket_name)
        CfnOutput(self, "RollingWindowTableName", value=windows.table_name)
        CfnOutput(self, "AppSecretArn", value=app_secret.secret_arn)
        CfnOutput(self, "RdsSecretArn", value=rds_secret.secret_arn)
        CfnOutput(self, "MigrationTaskDefinitionArn", value=migration_task.task_definition_arn)
        CfnOutput(self, "TaskSecurityGroupId", value=task_sg.security_group_id)
        CfnOutput(self, "McpInternalUrl", value=AWS_MCP_SERVER_URL)
        CfnOutput(self, "PublicSubnetIds", value=",".join(public_subnets))
        CfnOutput(self, "IsolatedSubnetIds", value=",".join(isolated_subnets))
        CfnOutput(
            self,
            "PublicIpDemoNote",
            value=(
                "assign_public_ip=True on a public subnet is a cost-saving demo choice "
                "(no NAT Gateway). It is not a production recommendation."
            ),
        )
        CfnOutput(
            self,
            "WhatsAppLimitation",
            value=(
                "Inbound WhatsApp is disabled on AWS: Meta cannot reach the CIDR-restricted ALB. "
                "Do not open 0.0.0.0/0 to expose a webhook."
            ),
        )
        CfnOutput(
            self,
            "DestroyWarning",
            value=(
                "Demo destroy deletes RDS data and the DynamoDB table. Empty StatementBucketName "
                "before cdk destroy; a non-empty bucket will fail deletion. "
                "RDS, ALB, ECS, Secrets Manager, CloudWatch Logs, and DynamoDB incur charges while deployed. "
                if demo_destroy
                else "This environment name is not destroy-compatible; removal policies retain data."
            ),
        )


def stack_environment() -> Environment | None:
    account = os.environ.get("CDK_DEFAULT_ACCOUNT")
    region = os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "eu-west-2"
    if not account:
        return None
    return Environment(account=account, region=region)
