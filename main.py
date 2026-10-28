"""BugBait: plant bugs in a project and see which ones its tests miss."""

from runner import Sandbox

TARGET = "calculator"

# Hardcoded bugs for now; the Saboteur agent will generate these later
BUGS = [
    {"file": "calc.py", "old": "a + b", "new": "a - b"},
    {"file": "calc.py", "old": "age >= 18", "new": "age > 18"},
    {"file": "calc.py", "old": "percent > 100", "new": "percent >= 100"},
]


def main():
    with Sandbox(TARGET) as sb:
        passed, output = sb.run_tests()
    if not passed:
        print("Baseline tests fail. Fix the target first.\n")
        print(output)
        return
    print("Baseline tests pass\n")

    survived = 0
    for i, bug in enumerate(BUGS, 1):
        with Sandbox(TARGET) as sb:
            sb.apply_mutation(bug["file"], bug["old"], bug["new"])
            passed, _ = sb.run_tests()
        if passed:
            survived += 1
            print(f"Bug {i} SURVIVED: {bug['old']} -> {bug['new']}")
        else:
            print(f"Bug {i} caught:    {bug['old']} -> {bug['new']}")

    caught = len(BUGS) - survived
    print(f"\nScore: {caught}/{len(BUGS)} bugs caught ({caught * 100 // len(BUGS)}%)")


if __name__ == "__main__":
    main()
