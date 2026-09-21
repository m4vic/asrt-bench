"""Entry point: `python -m asrtbench`.

Two ways to run it, both supported:

    pip install asrt-bench            # then:  asrt-bench
    # or, straight from a clone:
    git clone https://github.com/m4vic/asrt-bench
    cd asrt-bench
    pip install -r requirements.txt
    python -m asrtbench

The clone path stays first-class on purpose -- the attack packs are plain JSON
files people are meant to read, copy and extend, which is easier with the repo
in front of you.
"""

from asrtbench.cli import main

if __name__ == "__main__":
    main()
