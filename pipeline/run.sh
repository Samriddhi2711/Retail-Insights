#!/usr/bin/env bash
# ===========================================================================
# Apex Retail Store Intelligence — Pipeline Runner
# ===========================================================================
# Processes CCTV footage through the detection pipeline.
# Outputs events.jsonl conforming to the canonical schema (AGENTS.md).
#
# Usage:
#   ./pipeline/run.sh                                    # All cameras
#   ./pipeline/run.sh --config pipeline/pipeline_config.yaml
#   ./pipeline/run.sh --camera CAM_ENTRY_01              # Single camera
#
# Reference: AGENTS.md § Project Structure
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=============================================="
echo " Apex Retail Store Intelligence Pipeline"
echo "=============================================="
echo " Project root: $PROJECT_ROOT"
echo " Start time:   $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "=============================================="

# Change to project root so relative paths in config work
cd "$PROJECT_ROOT"

# Run the pipeline
python -m pipeline.main "$@"

EXIT_CODE=$?

echo "=============================================="
echo " Pipeline finished with exit code: $EXIT_CODE"
echo " End time: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "=============================================="

exit $EXIT_CODE
