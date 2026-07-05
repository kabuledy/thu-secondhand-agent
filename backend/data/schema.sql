-- ================================================================
-- 数据库 Schema — Supabase (PostgreSQL) / 本地 PostgreSQL
--
-- 使用方法：
--   方案A（Supabase）：在 Supabase 控制台 SQL Editor 中执行
--   方案B（本地）：psql -U your_user -d your_db -f schema.sql
-- ================================================================

-- ── 启用 UUID 扩展 ──
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";          -- 用于文本相似度搜索
CREATE EXTENSION IF NOT EXISTS "vector";            -- 用于向量检索（需要 pgvector 插件）

-- ── 分类枚举 ──
CREATE TYPE item_category AS ENUM (
    '交通工具', '电子产品', '书籍', '家具',
    '衣物', '体育用品', '生活用品', '其他'
);

CREATE TYPE item_status AS ENUM (
    'active',    -- 在售
    'sold',      -- 已售出
    'deleted'    -- 已删除（软删除）
);

CREATE TYPE contact_method AS ENUM (
    'wechat',     -- 微信
    'phone',      -- 手机号
    'email',      -- 邮箱
    'in_person'   -- 当面交易
);

-- ── 商品表 ──
CREATE TABLE items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id         VARCHAR(32) UNIQUE NOT NULL,       -- 对外展示 ID：ITEM-20260703-XXXX
    name            VARCHAR(200) NOT NULL,              -- 物品名称
    description     TEXT NOT NULL,                       -- 详细描述
    price           VARCHAR(50),                         -- 价格（字符串，允许"面议"等）
    category        item_category DEFAULT '其他',        -- 分类
    tags            TEXT[] DEFAULT '{}',                 -- 标签数组
    image_url       TEXT,                                -- 图片 URL
    image_description TEXT,                              -- 图片分析结果
    contact_type    contact_method NOT NULL,             -- 联系方式类型
    contact_value   VARCHAR(200) NOT NULL,               -- 联系方式内容
    status          item_status DEFAULT 'active',        -- 状态
    created_at      TIMESTAMPTZ DEFAULT NOW(),           -- 创建时间
    updated_at      TIMESTAMPTZ DEFAULT NOW()            -- 更新时间
);

-- ── 向量表（用于语义搜索，单独存储避免主表过重） ──
CREATE TABLE item_embeddings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id         VARCHAR(32) REFERENCES items(item_id) ON DELETE CASCADE,
    embedding       VECTOR(768),                         -- 向量维度（智谱 Embedding-2 是 768 维）
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── 索引 ──
CREATE INDEX idx_items_status ON items(status);
CREATE INDEX idx_items_category ON items(category);
CREATE INDEX idx_items_created_at ON items(created_at DESC);
CREATE INDEX idx_items_name_trgm ON items USING gin (name gin_trgm_ops);  -- 模糊搜索
CREATE INDEX idx_items_description_trgm ON items USING gin (description gin_trgm_ops);
CREATE INDEX idx_item_embeddings ON item_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ── 更新时间触发器 ──
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_items_updated_at
    BEFORE UPDATE ON items
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ── 软删除视图（查询时只查在售商品） ──
CREATE VIEW active_items AS
SELECT * FROM items WHERE status = 'active'
ORDER BY created_at DESC;

-- ================================================================
-- 迁移从内存 JSON → PostgreSQL
--
-- data/items_db.json 中的数据可以通过以下方式导入：
--
-- 方式1: 使用 pgAdmin / Supabase 的导入功能
-- 方式2: 运行迁移脚本 python scripts/migrate_to_supabase.py
--
-- 迁移脚本地址将在后续提供。
-- ================================================================

-- 给个注释提醒自己
COMMENT ON TABLE items IS 'THU 二手集市 · 商品主表';
COMMENT ON COLUMN items.contact_value IS '用加密/脱敏存储生产环境建议';
COMMENT ON VIEW active_items IS '只在售商品的视图，默认按发布时间倒序';
