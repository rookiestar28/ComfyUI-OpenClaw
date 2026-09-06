import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time

# Config
TARGET_FILES = ["services/access_control.py"]
# IMPORTANT: mutation gate must exercise all access-control invariants.
# Running only tests.test_access_control misses RBAC/CSRF/tier contracts and
# lets critical mutants survive.
# CRITICAL: do not reduce this suite to a single module.
# Regressing to tests.test_access_control alone reproduces R113 false-pass risk:
# mutation score collapses (~27%) and CI adversarial extended gate fails.
TEST_COMMAND = [
    sys.executable,
    "-m",
    "unittest",
    "tests.test_access_control",
    "tests.security.test_rbac",
    "tests.test_csrf_loopback",
    "tests.test_s34_observability_tiers",
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mutation_test")


class MutationVisitor(ast.NodeTransformer):
    def __init__(self):
        self.mutations = []
        self.current_mutation_index = -1
        self.mutation_counter = 0
        self.applied_desc = None

    def visit_Compare(self, node):
        # Mutate '==' to '!=' and vice versa
        if len(node.ops) == 1:
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                self._maybe_mutate(
                    node, lambda n: [setattr(n, "ops", [ast.NotEq()])], "Eq -> NotEq"
                )
            elif isinstance(op, ast.NotEq):
                self._maybe_mutate(
                    node, lambda n: [setattr(n, "ops", [ast.Eq()])], "NotEq -> Eq"
                )
        return self.generic_visit(node)

    def visit_BoolOp(self, node):
        # Mutate 'and' to 'or' and vice versa
        op = node.op
        if isinstance(op, ast.And):
            self._maybe_mutate(node, lambda n: setattr(n, "op", ast.Or()), "And -> Or")
        elif isinstance(op, ast.Or):
            self._maybe_mutate(node, lambda n: setattr(n, "op", ast.And()), "Or -> And")
        return self.generic_visit(node)

    def _maybe_mutate(self, node, action, desc):
        if self.current_mutation_index == self.mutation_counter:
            logger.debug(f"Applying mutation {self.mutation_counter}: {desc}")
            action(node)
            self.applied_desc = desc
        self.mutation_counter += 1


def count_mutations(file_path: str) -> int:
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    visitor = MutationVisitor()
    visitor.visit(tree)
    return visitor.mutation_counter


def apply_mutation(file_path: str, mutation_index: int) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    visitor = MutationVisitor()
    visitor.current_mutation_index = mutation_index
    visitor.visit(tree)

    # Write back
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(ast.unparse(tree))

    return getattr(visitor, "applied_desc", "Unknown")


def _purge_target_bytecode(target_file: str) -> None:
    """
    Remove target module bytecode cache before each subprocess test run.

    CRITICAL: mutation rewrites the same source file many times in rapid
    succession. On some filesystems/timestamp granularities, stale .pyc reuse
    can misclassify baseline/mutant outcomes.
    """
    target_dir = os.path.dirname(target_file)
    module_name = os.path.splitext(os.path.basename(target_file))[0]
    pycache_dir = os.path.join(target_dir, "__pycache__")
    if not os.path.isdir(pycache_dir):
        return
    prefix = module_name + "."
    for entry in os.listdir(pycache_dir):
        if entry.startswith(prefix) and entry.endswith(".pyc"):
            try:
                os.remove(os.path.join(pycache_dir, entry))
            except OSError:
                # Best-effort cleanup; test subprocess will still run.
                pass


def run_test_suite(
    target_file: str, *, context: str, retry_on_failure: bool = False
) -> bool:
    try:
        attempts = 2 if retry_on_failure else 1
        for attempt in range(1, attempts + 1):
            _purge_target_bytecode(target_file)
            result = subprocess.run(
                TEST_COMMAND, capture_output=True, text=True, timeout=30
            )
            # IMPORTANT: treat "Ran 0 tests" as failure for mutation integrity.
            # A green return code with zero executed tests invalidates mutation score.
            combined = f"{result.stdout or ''}\n{result.stderr or ''}"
            m = re.search(r"Ran\s+(\d+)\s+tests", combined)
            tests_run = int(m.group(1)) if m else 0
            if tests_run == 0:
                logger.error(
                    "Mutation %s invalid: test command executed 0 tests. "
                    "Output tail: %s",
                    context,
                    combined[-500:].replace("\n", " "),
                )
                if retry_on_failure and attempt < attempts:
                    logger.warning(
                        "Retrying mutation %s test command (attempt %s/%s).",
                        context,
                        attempt + 1,
                        attempts,
                    )
                    time.sleep(0.2)
                    continue
                return False
            if result.returncode != 0:
                logger.error(
                    "Mutation %s test command failed (rc=%s). Output tail: %s",
                    context,
                    result.returncode,
                    combined[-500:].replace("\n", " "),
                )
                if retry_on_failure and attempt < attempts:
                    logger.warning(
                        "Retrying mutation %s test command (attempt %s/%s).",
                        context,
                        attempt + 1,
                        attempts,
                    )
                    time.sleep(0.2)
                    continue
                return False
            return True
        return False
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        logger.error(f"Test run failed: {e}")
        return False


def run_mutation_workflow() -> bool:
    report = {"total_mutants": 0, "killed": 0, "survived": 0, "details": []}
    processed_targets = 0

    for target in TARGET_FILES:
        target_path = os.path.abspath(target)
        backup_path = target_path + ".bak"

        if not os.path.exists(target_path):
            logger.error(f"Target file not found: {target}")
            return False

        processed_targets += 1

        logger.info(f"Targeting {target}...")

        # 1. Check baseline
        # shutil.copy2(target_path, backup_path) # Backup first just in case
        # But wait, run_test_suite runs on current state.

        logger.info("Running baseline tests...")
        if not run_test_suite(target_path, context="baseline", retry_on_failure=True):
            # CRITICAL: baseline failure must fail the process with non-zero exit.
            # Returning success here would let upstream gates treat mutation as pass.
            logger.error("Baseline tests failed! Cannot proceed with mutation testing.")
            return False

        # 2. Count mutations
        num_mutations = count_mutations(target_path)
        logger.info(f"Found {num_mutations} mutation points in {target}")
        report["total_mutants"] += num_mutations

        # 3. Apply mutations one by one
        try:
            shutil.copy2(target_path, backup_path)

            for i in range(num_mutations):
                # Restore clean
                shutil.copy2(backup_path, target_path)

                # Apply mutation
                desc = apply_mutation(target_path, i)
                logger.info(f"Mutant {i+1}/{num_mutations}: {desc}")

                # Run tests
                passed = run_test_suite(target_path, context="mutant")

                if not passed:
                    logger.info("-> KILLED")
                    report["killed"] += 1
                else:
                    logger.warning("-> SURVIVED")
                    report["survived"] += 1
                    report["details"].append(
                        {
                            "file": target,
                            "mutation_index": i,
                            "description": desc,
                            "status": "SURVIVED",
                        }
                    )
        finally:
            # Restore original
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, target_path)
                os.remove(backup_path)
                logger.info("Restored original file.")

    if processed_targets == 0:
        logger.error("No mutation targets processed.")
        return False

    # Generate Report
    score = 0
    if report["total_mutants"] > 0:
        score = (report["killed"] / report["total_mutants"]) * 100

    logger.info("=" * 40)
    logger.info(f"Mutation Score: {score:.2f}%")
    logger.info(
        f"Total: {report['total_mutants']}, Killed: {report['killed']}, Survived: {report['survived']}"
    )
    logger.info("=" * 40)

    report_file = os.path.join(".planning", "mutation_report.json")
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved to {report_file}")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run_mutation_workflow() else 1)
