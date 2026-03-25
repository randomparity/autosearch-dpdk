"""DPDK plugin for autoforge — meson/ninja builds, testpmd and DTS testing."""

from __future__ import annotations

from autoforge_dpdk.builder import DpdkBuilder
from autoforge_dpdk.deployer import DpdkDeployer
from autoforge_dpdk.tester import DpdkTester


class DpdkPlugin:
    """Autoforge plugin for DPDK optimization."""

    name = "dpdk"

    def create_builder(self) -> DpdkBuilder:
        return DpdkBuilder()

    def create_deployer(self) -> DpdkDeployer:
        return DpdkDeployer()

    def create_tester(self) -> DpdkTester:
        return DpdkTester()
