# 部署指南

> 从零开始，把你的智能体部署到线上

---

## 目录

```
1. 整体架构图
2. 你需要准备的东西
3. 方案A：腾讯云函数部署（推荐）
4. 方案B：Vercel Serverless 部署
5. 在清小搭平台配置工具函数
6. 配置图片存储（腾讯云 COS）
7. 配置 API Key
8. 测试验证
9. 常见问题
```

---

## 1. 整体架构图

```
用户 → 清小搭智能体（LLM）
              │
              ▼（通过工具函数调用 API）
     ┌────────────────────────┐
     │  你的后端服务（云函数） │
     │  main.py               │
     └────────┬───────────────┘
              │
     ┌────────┴───────────────┐
     │   数据库（JSON文件/云表） │
     └────────────────────────┘
```

> 你只需要部署一次后端，然后在清小搭配 5 个工具函数即可。

---

## 2. 你需要准备的东西

### 2.1 注册清小搭平台（你已经有了）
用清华账号登录智能体广场。

### 2.2 注册云服务（二选一）
| 方案 | 费用 | 适合 |
|------|------|------|
| **腾讯云函数** | 免费额度够用 | 推荐，国内访问快 |
| **Vercel** | 免费 | 需要科学上网 |

### 2.3 获取 API Key

| 服务 | 注册地址 | 费用 | 用途 |
|------|---------|------|------|
| **DeepSeek** (必需) | https://platform.deepseek.com/ | 注册送额度 | 视觉分析 + 联网搜索 |
| **SiliconFlow** (推荐) | https://siliconflow.cn/ | 免费额度 | 语义搜索 Embedding |

> 最低只需要注册 **DeepSeek** 一个账号即可跑通核心功能。
> 建议同时注册 **SiliconFlow** 开启语义搜索（搜"代步工具"找到"自行车"）。

---

## 3. 方案A：腾讯云函数部署（推荐）

### 3.1 创建云函数

1. **登录** [腾讯云控制台](https://console.cloud.tencent.com/)
2. **进入** 云函数（Serverless Cloud Function）
3. **新建** → 选择"自定义创建"
   - 函数名称：`thu-secondhand-agent`
   - 运行环境：**Python 3.9** 或 3.10
   - 创建方式：**在线编辑**
4. **上传代码**
   - 点击"提交方法" → 选择"本地上传文件夹"
   - 上传整个 `backend/` 目录（含 `main.py`、`api/`、`data/`）
5. **修改入口**
   - 云函数入口文件需改为 `main.py`
   - 入口函数：`main.app`（Flask 应用实例）
   - 启动方式参考：腾讯云 Flask 框架模板

> ⚠️ **特别注意**：腾讯云函数的文件系统是临时的，JSON 文件存的数据可能会丢失。
> 如果想持久保存，后面建议升级到 Supabase 数据库。MVP 阶段先用 JSON 跑没问题。

### 3.2 配置环境变量

在云函数"函数配置"中设置：

| 变量名 | 值 | 必填 |
|--------|-----|------|
| `DEEPSEEK_API_KEY` | 你的 DeepSeek API Key | ✅ |
| `SILICONFLOW_API_KEY` | 你的 SiliconFlow API Key | ❌ |

### 3.3 获取 API 地址

部署成功后，腾讯云会给你一个 **访问路径（URL）**，类似：
```
https://service-xxxxxxx-xxxxxxxxxx.gz.apigw.tencentcs.com/release/
```

记下这个 URL，下一步要用。

---

## 4. 方案B：Vercel Serverless 部署

### 4.1 安装依赖

```bash
# 如果你电脑有 Python 环境
pip install vercel
```

### 4.2 在 backend/ 目录下创建 vercel.json

```json
{
  "builds": [{"src": "main.py", "use": "@vercel/python"}],
  "routes": [{"src": "/(.*)", "dest": "main.py"}]
}
```

> 我已经帮你在 deploy/ 下准备了 vercel.json，复制到 backend/ 目录。

### 4.3 部署

```bash
cd backend/
vercel --prod
```

### 4.4 配置环境变量

在 Vercel 项目 Settings → Environment Variables 中添加：
- `DEEPSEEK_API_KEY`
- `SILICONFLOW_API_KEY`（可选）

### 4.5 获取 URL

部署完成后 Vercel 会返回类似：
```
https://thu-secondhand-agent.vercel.app
```

---

## 5. 在清小搭平台配置工具函数

这是最关键的一步。登录清小搭后找到智能体编辑页面：

### 5.1 进入工具配置

```
智能体管理 → 找到你的智能体 → 工具配置
```

### 5.2 添加 5 个工具

每个工具配置如下（假设你的后端地址是 `https://your-api.com`）：

---

**工具1：发布商品**

| 字段 | 值 |
|------|----|
| 工具名称 | `list_item` |
| 工具描述 | 卖家发布二手物品，保存到数据库。调用此工具前请先通过对话引导用户提供完整信息 |
| API 地址 | `https://your-api.com/api/list_item` |
| 请求方式 | POST |
| 请求参数 | `{"name": "...", "description": "...", "contact_type": "wechat", "contact_value": "..."}` |

---

**工具2：搜索商品**

| 字段 | 值 |
|------|----|
| 工具名称 | `search_item` |
| 工具描述 | 根据买家自然语言描述搜索匹配的商品，返回按符合度排序的结果 |
| API 地址 | `https://your-api.com/api/search_item` |
| 请求方式 | POST |
| 请求参数 | `{"query": "我要找通勤用的自行车"}` |

---

**工具3：分析图片**

| 字段 | 值 |
|------|----|
| 工具名称 | `analyze_image` |
| 工具描述 | 分析商品图片，返回物品的类别、颜色、成色等特征 |
| API 地址 | `https://your-api.com/api/analyze_image` |
| 请求方式 | POST |
| 请求参数 | `{"image_url": "https://..."}` |

---

**工具4：网络搜索**

| 字段 | 值 |
|------|----|
| 工具名称 | `web_search` |
| 工具描述 | 搜索网络信息，用于为卖家的物品生成介绍文案 |
| API 地址 | `https://your-api.com/api/web_search` |
| 请求方式 | POST |
| 请求参数 | `{"query": "山地自行车 用途 特点"}` |

---

**工具5：商品详情**

| 字段 | 值 |
|------|----|
| 工具名称 | `get_item_detail` |
| 工具描述 | 根据商品编号获取完整信息（含联系方式） |
| API 地址 | `https://your-api.com/api/get_item_detail` |
| 请求方式 | POST |
| 请求参数 | `{"item_id": "ITEM-20260703-0001"}` |

---

**工具6：更新状态**

| 字段 | 值 |
|------|----|
| 工具名称 | `update_status` |
| 工具描述 | 标记商品已售出或下架 |
| API 地址 | `https://your-api.com/api/update_status` |
| 请求方式 | POST |
| 请求参数 | `{"item_id": "ITEM-20260703-0001", "status": "sold"}` |

---

**工具7：按标签搜索**

| 字段 | 值 |
|------|----|
| 工具名称 | `search_by_tag` |
| 工具描述 | 按标签搜索商品，返回所有带有此标签的在售物品。当用户点击推荐标签或直接输入标签名时调用 |
| API 地址 | `https://your-api.com/api/search_by_tag` |
| 请求方式 | POST |
| 请求参数 | `{"tag": "文具"}` |

---

**工具8：获取热门标签**

| 字段 | 值 |
|------|----|
| 工具名称 | `get_popular_tags` |
| 工具描述 | 获取当前使用频率最高的3个标签，用于在聊天界面推荐展示。每次用户第一次发消息时调用一次 |
| API 地址 | `https://your-api.com/api/get_popular_tags` |
| 请求方式 | GET |
| 请求参数 | 无需参数 |

### 5.3 保存配置

保存后，清小搭的 LLM 就能自动调用这些工具了。

---

## 6. 配置图片存储（腾讯云 COS）

> 只有需要**上传图片**功能时才需要配这个。纯文字聊天可以跳过。

### 6.1 创建 COS 存储桶

1. **登录** [腾讯云控制台](https://console.cloud.tencent.com/) → **对象存储 COS**
2. **创建存储桶**（Bucket）：
   - 名称：`thu-secondhand-images`（或你喜欢的名字）
   - 所属地域：选离你最近的地域，如 `广州`
   - 访问权限：**公有读私有写**（这样图片链接才能被访问）
   - 其他默认即可
3. 创建成功后，记下**存储桶名称**和**所属地域**

### 6.2 获取 API 密钥

1. 进入 [API 密钥管理](https://console.cloud.tencent.com/cam/capi)
2. 点击"新建密钥"，生成 `SecretId` 和 `SecretKey`
3. 把这两个值复制下来

### 6.3 配置环境变量

在云函数/Vercel 的环境变量中，加上以下 4 项：

| 变量名 | 值 | 示例 |
|--------|-----|------|
| `COS_BUCKET` | 你的存储桶名称 | `thu-secondhand-images` |
| `COS_REGION` | 存储桶地域 | `ap-guangzhou` |
| `COS_SECRET_ID` | API 密钥 ID | `AKIDxxxxxxxx` |
| `COS_SECRET_KEY` | API 密钥 Key | `xxxxxxxx` |

配置好后，重启云函数/Vercel 部署，上传的图片就会自动存到 COS 中。

> 💡 **没有配 COS 会怎样？** 默认使用本地文件系统（`data/uploads/`）。
> 开发环境完全正常。但云函数的临时文件系统重启后会清空图片，
> 所以**生产环境一定要配 COS**。

### 6.4（可选）开启 CDN 加速

在 COS 控制台开启"默认 CDN 加速"，可以大幅提升图片加载速度。
开启后在环境变量中增加：
| 变量名 | 值 |
|--------|-----|
| `PUBLIC_BASE` | 你的 CDN 域名，如 `https://images.your-domain.com` |


## 7. 配置 API Key

> 如果你选择智谱 AI（推荐），以下是在智谱开放平台的配置步骤。

### 6.1 注册智谱 AI

1. 打开 https://open.bigmodel.cn/
2. 用手机号注册登录
3. 进入"API Keys"页面
4. 点击"添加 API Key"
5. 复制生成的 Key（以 `xxx.xxx` 格式）

### 6.2 配置

把 Key 设置到环境变量（腾讯云函数/Vercel的配置页面）。

**测试 API 是否可用：**

```bash
curl https://open.bigmodel.cn/api/paas/v4/chat/completions \
  -H "Authorization: Bearer your_zhipu_api_key" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-4-flash","messages":[{"role":"user","content":"你好"}]}'
```

如果能返回结果，说明 API Key 配置正确。

---

## 8. 测试验证

配置完成后，进行完整的端到端测试：

### 测试用例 1：卖家发布

```
用户：我想卖我的山地自行车
智能体：→ 好的，请问你的自行车是什么品牌和型号？
用户：蓝色山地车，26寸
智能体：→ 有照片吗？
用户：[上传图片]
智能体：→ 从照片看这是一台蓝色山地自行车，看起来有九成新，对吗？
用户：对的
智能体：→ 我来帮你生成一段介绍？还是你自己写？
用户：你帮我写
智能体：→ [调用 web_search] → 生成草稿
         这是我帮你写的草稿，你看看：
         "蓝色山地自行车，26寸，九成新，变速正常，通勤代步够用"
         需要修改吗？
用户：不用，再加一句"送车锁"就行
智能体：→ 好的。请问你希望买家通过什么方式联系你？
用户：微信吧，thu_bike_2024
智能体：→ [调用 list_item]
         🎉 发布成功！物品编号：ITEM-20260703-0001
```

### 测试用例 2：买家搜索

```
用户：我想找一辆通勤用的自行车
智能体：→ [调用 search_item]
         为你找到以下物品：
         🥇 匹配度 95%
         📦 蓝色山地自行车 | ¥300
         📝 九成新，26寸，通勤代步
         ...
用户：详情1
智能体：→ [调用 get_item_detail]
         ── 完整信息 ──
         联系方式：微信 thu_bike_2024
         💡 建议线下当面交易，选校内公共场所
```

### 测试用例 3：无匹配

```
用户：有没有二手特斯拉？
智能体：→ [调用 search_item，返回空]
         暂时没有找到「特斯拉」相关的物品 😅
         换个关键词试试？
```

如果三个测试用例都能跑通，你的智能体就上线成功了！

---

## 9. 常见问题

### Q: 我的后端部署好了，但清小搭调用时报错 404？
A: 检查 API 地址是否正确，地址末尾不要带 `/`。然后在浏览器访问 `https://your-api.com/` 看是否返回 `{"status": "ok"}`。

### Q: 图片分析不起作用？
A: 检查 `ZHIPU_API_KEY` 是否配置正确。智谱的 GLM-4V 需要单独的权限，确保账号已开通。

### Q: 搜索不准确？
A: MVP 阶段使用关键词匹配，精度有限。后续接入语义向量检索（Embedding）后会有明显提升。在 environment 中配置好 API Key 后会自动启用。

### Q: 数据重启后丢了？
A: 因为 MVP 阶段用了 JSON 文件存储，云函数的临时文件系统在重启后会清空。建议升级到 Supabase（免费）做持久存储。部署指南中有迁移方案。

### Q: 部署遇到问题怎么办？
A: 直接把报错信息发给我，我来帮你排查。
