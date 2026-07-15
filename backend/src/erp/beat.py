"""beat 进程入口：python -m erp.beat（compose beat 角色，R2-04）。"""

from erp.automation.beat import main

if __name__ == "__main__":
    raise SystemExit(main())
