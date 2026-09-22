#!/usr/bin/env python3
"""`python3 install.py --uninstall`의 별칭. 원복이 필요한 사람이 찾는 이름이다."""
import sys

import install

if __name__ == "__main__":
    sys.exit(install.main(sys.argv[1:] + ["--uninstall"]))
