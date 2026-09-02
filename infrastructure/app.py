#!/usr/bin/env python3
from __future__ import annotations

import os

from aws_cdk import App
from aws_cdk import aws_ecs as ecs

from stacks.cidr import require_allowed_cidr
from stacks.tlh_stack import TaxLossHarvestingStack, stack_environment

app = App(outdir=os.environ.get("CDK_OUTDIR", "cdk.out"))
environment_name = (
    app.node.try_get_context("environment")
    or os.environ.get("ENVIRONMENT")
    or "demo"
)
prebuilt = app.node.try_get_context("container_image") or os.environ.get("TLH_CONTAINER_IMAGE")
container_image = ecs.ContainerImage.from_registry(str(prebuilt)) if prebuilt else None
TaxLossHarvestingStack(
    app,
    "TaxLossHarvestingDemo",
    allowed_ipv4_cidr=require_allowed_cidr(app=app),
    environment_name=str(environment_name),
    container_image=container_image,
    env=stack_environment(),
)
app.synth()
