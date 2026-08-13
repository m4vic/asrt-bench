"""Entry point: `python -m asrtbench`.

asrt-bench is a clone-and-run tool, not a published package -- so the canonical
way to launch it is from the cloned repo, no install of the package itself:

    git clone https://github.com/m4vic/asrt-bench
    cd asrt-bench
    pip install -r requirements.txt
    python -m asrtbench
"""

from asrtbench.cli import main

if __name__ == "__main__":
    main()
