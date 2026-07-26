# SAM custom build targets.
#
# The Lambda artifact is flat — backend/ contents land at the package root — so
# rubric.json has to be copied in explicitly. rubric/rubric.json stays the single
# source of truth; backend/rubric.py looks in both locations.

build-ApiFunction: build-common
build-WorkerFunction: build-common

build-common:
	python -m pip install --quiet --requirement requirements.txt --target "$(ARTIFACTS_DIR)"
	cp *.py "$(ARTIFACTS_DIR)/"
	cp -r ingestion agent api "$(ARTIFACTS_DIR)/"
	cp ../rubric/rubric.json "$(ARTIFACTS_DIR)/rubric.json"
	find "$(ARTIFACTS_DIR)" -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf "$(ARTIFACTS_DIR)/tests" "$(ARTIFACTS_DIR)/conftest.py"
