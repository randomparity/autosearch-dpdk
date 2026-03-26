#!/usr/bin/env bash
set -euo pipefail

# Setup passwordless sudo for the autoforge runner.
#
# This creates a sudoers drop-in that allows the runner user to execute
# only the specific commands needed: testpmd binary and perf tools.
#
# Usage:
#   sudo ./scripts/setup-sudo.sh [username] [build_dir]
#
# Arguments:
#   username   - user that runs the runner service (default: current user)
#   build_dir  - DPDK build directory (default: /tmp/dpdk-build)
#
# What gets added to sudoers:
#   - <build_dir>/app/dpdk-testpmd  (testpmd binary)
#   - /usr/bin/perf                 (perf record/stat/script)

RUNNER_USER="${1:-${SUDO_USER:-$(whoami)}}"
BUILD_DIR="${2:-/tmp/dpdk-build}"
SUDOERS_FILE="/etc/sudoers.d/autoforge"
TESTPMD_BIN="${BUILD_DIR}/app/dpdk-testpmd"
PERF_BIN="$(command -v perf 2>/dev/null || echo /usr/bin/perf)"

if [[ $EUID -ne 0 ]]; then
    echo "Error: this script must be run as root (or with sudo)"
    exit 1
fi

echo "Setting up sudoers for user: ${RUNNER_USER}"
echo "  testpmd: ${TESTPMD_BIN}"
echo "  perf:    ${PERF_BIN}"

TMPFILE=$(mktemp /etc/sudoers.d/.autoforge-tmp-XXXXXX)
trap 'rm -f "$TMPFILE"' EXIT

cat > "${TMPFILE}" <<EOF
# autoforge runner: allow testpmd and perf without password
${RUNNER_USER} ALL=(ALL) NOPASSWD: ${TESTPMD_BIN}
${RUNNER_USER} ALL=(ALL) NOPASSWD: ${PERF_BIN}
EOF

chmod 0440 "${TMPFILE}"

# Validate before installing
if visudo -cf "${TMPFILE}"; then
    mv "${TMPFILE}" "${SUDOERS_FILE}"
    trap - EXIT
    echo "Sudoers file installed: ${SUDOERS_FILE}"
else
    echo "Error: sudoers validation failed"
    exit 1
fi

echo ""
echo "Verify with:"
echo "  sudo -u ${RUNNER_USER} sudo -n ${TESTPMD_BIN} --version"
echo "  sudo -u ${RUNNER_USER} sudo -n ${PERF_BIN} --version"
