"""Infrastructure-as-code checks that do not need a cloud account.

None of this Terraform has ever been applied (ADR-0005), so these tests guard
the properties a `terraform validate` cannot see: that secrets are not
committed, that public access is off by default, that production differs from
development in durability rather than in shape.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = ROOT / "infrastructure" / "terraform"
MODULES = TERRAFORM / "modules"
ENVIRONMENTS = TERRAFORM / "environments"


def terraform_files() -> list[Path]:
    return sorted(TERRAFORM.rglob("*.tf"))


class TestLayout:
    @pytest.mark.parametrize("module", ["networking", "eks", "kafka", "clickhouse", "monitoring"])
    def test_each_capability_has_a_module(self, module: str) -> None:
        assert (MODULES / module / "main.tf").is_file()

    @pytest.mark.parametrize("module", ["networking", "eks", "kafka", "clickhouse", "monitoring"])
    def test_modules_declare_their_interface(self, module: str) -> None:
        """A module without variables and outputs is a copy-paste in disguise."""
        assert (MODULES / module / "variables.tf").is_file()
        assert (MODULES / module / "outputs.tf").is_file()

    def test_the_aws_environment_is_complete(self) -> None:
        aws = ENVIRONMENTS / "aws"
        for name in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
            assert (aws / name).is_file(), f"aws environment missing {name}"
        assert (aws / "bootstrap.sh").is_file()
        assert (aws / "terraform.tfvars.example").is_file()


class TestSecrets:
    def test_no_terraform_file_contains_a_literal_secret(self) -> None:
        """The most expensive mistake in this repository would be a committed key."""
        patterns = [
            re.compile(r"AKIA[0-9A-Z]{16}"),
            re.compile(r"password\s*=\s*\"(?!\$\{|var\.)[^\"]{8,}\"", re.IGNORECASE),
            re.compile(r"secret_key\s*=\s*\"[^\"]{8,}\""),
        ]
        for path in terraform_files():
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                assert not pattern.search(text), f"possible secret in {path.relative_to(ROOT)}"

    def test_password_variables_are_marked_sensitive(self) -> None:
        text = (ENVIRONMENTS / "aws" / "variables.tf").read_text(encoding="utf-8")
        for block in text.split('variable "')[1:]:
            name = block.split('"')[0]
            if "password" in name:
                assert "sensitive   = true" in block, f"{name} is not marked sensitive"

    def test_the_example_tfvars_contains_no_passwords(self) -> None:
        text = (ENVIRONMENTS / "aws" / "terraform.tfvars.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("#"):
                continue
            assert "password" not in line.lower(), f"password in the example file: {line}"

    def test_terraform_state_and_tfvars_are_gitignored(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("*.tfstate", "terraform.tfvars", ".terraform/"):
            assert pattern in ignored, f"{pattern} is not gitignored"


class TestSafeDefaults:
    def test_the_kubernetes_api_is_private_by_default(self) -> None:
        eks = (MODULES / "eks" / "variables.tf").read_text(encoding="utf-8")
        block = eks.split('variable "endpoint_public_access"')[1].split("}")[0]
        assert "default     = false" in block

    def test_storage_is_encrypted(self) -> None:
        main = (ENVIRONMENTS / "aws" / "main.tf").read_text(encoding="utf-8")
        assert "storage_encrypted     = true" in main
        assert "at_rest_encryption_enabled = true" in main

    def test_the_telemetry_bucket_blocks_public_access(self) -> None:
        main = (ENVIRONMENTS / "aws" / "main.tf").read_text(encoding="utf-8")
        block = main.split('resource "aws_s3_bucket_public_access_block"')[1].split("\n}")[0]
        for setting in (
            "block_public_acls       = true",
            "block_public_policy     = true",
            "restrict_public_buckets = true",
        ):
            assert setting in block

    def test_instances_require_imdsv2(self) -> None:
        """Otherwise an SSRF becomes instance credentials."""
        clickhouse = (MODULES / "clickhouse" / "main.tf").read_text(encoding="utf-8")
        assert 'http_tokens                 = "required"' in clickhouse

    def test_kafka_does_not_auto_create_topics(self) -> None:
        """A typo should fail loudly, not create an empty topic nobody consumes."""
        kafka = (MODULES / "kafka" / "main.tf").read_text(encoding="utf-8")
        assert "auto.create.topics.enable=false" in kafka
        assert "unclean.leader.election.enable=false" in kafka

    def test_production_is_more_durable_than_development(self) -> None:
        """Prod must differ in durability, not in shape."""
        main = (ENVIRONMENTS / "aws" / "main.tf").read_text(encoding="utf-8")
        assert "multi_az                = local.is_prod" in main
        assert "backup_retention_period = local.is_prod ? 7 : 1" in main
        assert "deletion_protection        = local.is_prod" in main

    def test_raw_telemetry_tiers_rather_than_being_deleted_immediately(self) -> None:
        main = (ENVIRONMENTS / "aws" / "main.tf").read_text(encoding="utf-8")
        assert 'storage_class = "STANDARD_IA"' in main
        assert 'storage_class = "GLACIER"' in main


class TestDocumentation:
    def test_every_variable_is_described(self) -> None:
        """An undescribed variable is one an adopter has to read the code for."""
        for path in terraform_files():
            text = path.read_text(encoding="utf-8")
            for block in text.split('variable "')[1:]:
                name = block.split('"')[0]
                body = block.split("\n}")[0]
                assert "description" in body, f"{path.name}: variable {name} has no description"

    def test_every_output_is_described(self) -> None:
        for path in terraform_files():
            text = path.read_text(encoding="utf-8")
            for block in text.split('output "')[1:]:
                name = block.split('"')[0]
                body = block.split("\n}")[0]
                assert "description" in body, f"{path.name}: output {name} has no description"

    def test_the_environment_says_it_has_never_been_applied(self) -> None:
        """Honesty about what is verified is part of the deliverable."""
        readme = (ENVIRONMENTS / "aws" / "README.md").read_text(encoding="utf-8")
        assert "never been applied" in readme.lower()

    def test_outputs_cover_what_the_helm_chart_needs(self) -> None:
        outputs = (ENVIRONMENTS / "aws" / "outputs.tf").read_text(encoding="utf-8")
        for name in (
            "msk_bootstrap_brokers_tls",
            "clickhouse_endpoint",
            "postgres_endpoint",
            "redis_endpoint",
            "irsa_role_arn",
        ):
            assert f'output "{name}"' in outputs


class TestSecurityReviewFindings:
    """Three findings from a review of the first Terraform commit.

    Each is a control that looked reasonable in isolation and was wrong in
    combination, which is exactly the kind a checklist misses.
    """

    def test_clickhouse_requires_authentication(self) -> None:
        """The password variable was validated and then never used: the server
        came up with an unauthenticated default user behind a security group."""
        user_data = (MODULES / "clickhouse" / "user_data.sh.tftpl").read_text(encoding="utf-8")
        assert "password_sha256_hex" in user_data
        assert '<default remove="remove"/>' in user_data, "the passwordless default user must go"

        main = (ENVIRONMENTS / "aws" / "main.tf").read_text(encoding="utf-8")
        assert "password_sha256_hex = sha256(var.clickhouse_password)" in main

    def test_the_clickhouse_password_never_reaches_user_data_in_plaintext(self) -> None:
        """User data is readable by anything running on the instance."""
        variables = (MODULES / "clickhouse" / "variables.tf").read_text(encoding="utf-8")
        assert 'variable "password_sha256_hex"' in variables
        assert 'variable "password"' not in variables

    def test_redis_traffic_is_encrypted_in_transit(self) -> None:
        main = (ENVIRONMENTS / "aws" / "main.tf").read_text(encoding="utf-8")
        assert "transit_encryption_enabled = true" in main
        assert "auth_token                 = var.redis_auth_token" in main

    def test_a_public_kubernetes_api_requires_named_networks(self) -> None:
        """An enabled public endpoint with no CIDRs defaults to 0.0.0.0/0."""
        eks_main = (MODULES / "eks" / "main.tf").read_text(encoding="utf-8")
        assert "precondition" in eks_main
        assert "endpoint_public_access requires public_access_cidrs" in eks_main

        eks_vars = (MODULES / "eks" / "variables.tf").read_text(encoding="utf-8")
        assert '!contains(var.public_access_cidrs, "0.0.0.0/0")' in eks_vars
