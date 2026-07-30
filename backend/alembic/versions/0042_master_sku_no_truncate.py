"""R2-15 判据④⑤：master_sku 生成改为**永不截断**（修 lpad 截断撞号定时炸弹）

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-30

口径 D-Q72（SKU 内外分离，修订 D1）。本迁移只管 `master_sku` 的**生成式**，
`channel_sku` 的取值规则在 `listing/service.py`（同 PR 另一处改动）。

## 原缺陷：`lpad` 在串长超出目标时**从右截断**

```
现 DEFAULT: 'M' || lpad(nextval('app.master_sku_seq')::text, 7, '0')

序号        1  → lpad('1',       7,'0') = '0000001'  → M0000001
序号  1000000  → lpad('1000000', 7,'0') = '1000000'  → M1000000
序号 10000000  → lpad('10000000',7,'0') = '1000000'  → M1000000   ← 与上一行相同
```

即序号达 1000 万时与第 100 万号撞号。`master_sku` 有 UNIQUE 约束，故后果是
**产品入库整体报错停摆（硬停，不是静默劣化）**。

## 新生成式：短则左填零至 9 位，长则原样输出——**没有上界**

## 为什么不用 `to_char(nextval(...), 'FM000000000')`

那是本单踩点时的初版提议，**它不满足判据④**。PostgreSQL 的 numeric 格式模板在
数字位数超出模板时**返回一串 `#`**，判据④ 要的是「自动变长不截断」，而 `#`
既不是变长也不是原值——它只是把「撞号」换成「产出垃圾值」。

PG16 实测（本机，非引文）：

```
to_char(1,          'FM000000000') = 000000001
to_char(999999999,  'FM000000000') = 999999999
to_char(1000000000, 'FM000000000') = #########     ← 溢出，9 个 '#'
lpad(10000000::text, 7, '0')       = 1000000
lpad(1000000::text,  7, '0')       = 1000000       ← 原缺陷的撞号实测复现
```

## 为什么不用 `lpad(..., 9, '0')`

那只是把同一个截断 bug 从 1e7 挪到 1e9，一模一样的形状。

## 为什么用 PL/pgSQL 函数而不是单行表达式

要「只调一次 `nextval`、又要按值长度分两种输出」，表达式里就得引用该值两次，而
DEFAULT 表达式**不允许子查询**（PostgreSQL 限制），无法用 `FROM (SELECT nextval)` 兜。
纯 SQL 函数体带 FROM 子句虽可写，但**SQL 函数存在被内联的可能**，一旦内联，
两处 `v` 就可能变成两次 `nextval` 调用——那会跳号，甚至让同一行的两处取到不同值。
PL/pgSQL 有显式局部变量、不会被内联，`nextval` 严格只调一次。

## 存量一行不改（判据⑤）

只 `SET DEFAULT`，不 UPDATE 任何既有行——Walmart SKU 不可改，动存量等于毁掉已上架
listing 的对应关系。且**新旧格式位数不同（M+9 vs M+7）故永不相等**，新号不可能
撞上任何旧号，这一点由测试直接断言而不是靠本段推理。
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # VOLATILE：每次调用都要真的取下一个序号，不可被优化掉。
    # SECURITY INVOKER（默认）：调用者仍需 master_sku_seq 的 USAGE——与改造前
    # 直接在 DEFAULT 里调 nextval 的权限要求完全一致，故无需额外授权。
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.next_master_sku() RETURNS text
        LANGUAGE plpgsql VOLATILE AS $$
        DECLARE
          n bigint := nextval('app.master_sku_seq');
          s text   := n::text;
        BEGIN
          -- 只在不足 9 位时左填零；已达或超过 9 位一律原样输出（永不截断）。
          IF length(s) < 9 THEN
            s := lpad(s, 9, '0');
          END IF;
          RETURN 'M' || s;
        END
        $$;
        """
    )
    # 显式授权：PostgreSQL 默认把新函数的 EXECUTE 授予 PUBLIC，但依赖隐式默认在
    # 本项目已经栽过一次（0009 只授 SELECT/INSERT/UPDATE，14a 的 DELETE 因此 100% 失败）。
    op.execute("GRANT EXECUTE ON FUNCTION app.next_master_sku() TO erp_app;")
    op.execute("ALTER TABLE app.product ALTER COLUMN master_sku SET DEFAULT app.next_master_sku();")


def downgrade() -> None:
    # 回滚到原 lpad(7) 生成式——**它带着上述撞号缺陷**，仅为可逆性保留。
    # 注意：回滚后若序号已越过 1000 万，下一次入库即报 master_sku 唯一约束冲突。
    op.execute(
        "ALTER TABLE app.product ALTER COLUMN master_sku"
        " SET DEFAULT 'M' || lpad(nextval('app.master_sku_seq')::text, 7, '0');"
    )
    op.execute("DROP FUNCTION IF EXISTS app.next_master_sku();")
