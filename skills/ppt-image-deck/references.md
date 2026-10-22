# 输入与交接约定

输入为 UTF-8 编码的 `script.json`。根字段和逐页字段如下，额外字段随原文件保留。

| 字段 | 内容 |
| --- | --- |
| `schema_version` | 固定为 `"1.0"` |
| `title` | 文稿标题，非空字符串 |
| `canvas.width`、`canvas.height` | 像素尺寸，整数，范围 1 至 16384 |
| `slides` | 有序页面数组，1 至 500 页 |
| `slides[].id` | 1 至 64 个英文字母、数字、下划线或连字符；首字符为字母或数字；大小写不区分重复 |
| `title`、`key_message`、`image_prompt` | 每页标题、主张、视觉提示词，均为非空字符串 |
| `on_slide_text` | 上屏文字数组，可以为空；已有项为非空字符串 |
| `speaker_notes` | 演讲备注，字符串，可为空 |
| `source_refs` | 来源引用数组，可以为空；已有项为非空字符串，可以是明确标注的虚构示例来源 |

准备目录中 `manifest.json` 的类型为 `prepared_image_deck`。页面文件名由经过验证的 `id` 派生，`title` 和来源说明不参与路径构造。

生图后运行 `record_image.mjs`，为实际结果登记来源。记录保存为 `receipts/<id>.json`。必需信息为工具类型、来源说明、工具结果引用、模型版本、图片和提示词哈希。工具未提供模型版本时使用 `unknown`。该登记记录操作者提供的信息，真实性仍需与实际工具结果核对。

组装目录用 `build-manifest.json` 保存检查结果。其类型为 `assembled_image_deck`。记录包含 PPTX、图片、提示词和来源记录的哈希，以及检查状态。结构检查当前覆盖 PNG 文件头、数据块长度、CRC 校验和宽高比；PPTX 只检查 ZIP 文件头及输出存在。PNG 像素解码、OOXML 内容及 Office 显示效果仍需实际检查。

交接目录包括：

```text
image-handoff/
  script.json
  deck.pptx
  manifest.json
  build-manifest.json
  HANDOFF.md
  prompts/<id>.md
  assets/<id>.png
  receipts/<id>.json
```

交接 `manifest.json` 使用以下根字段。

```json
{ "schema_version": "1.0", "kind": "image_ppt_to_editable" }
```

主要字段如下。

| 字段 | 下一模块的用途 |
| --- | --- |
| `script_path`、`script_sha256` | 读取并校验确认过的脚本 |
| `deck_path`、`deck_sha256` | 读取并校验图片 PPT |
| `build_manifest_path`、`build_manifest_sha256` | 查看组装和审核状态 |
| `slides[]` | 保留原逐页字段、顺序和来源 |
| `prompt_path`、`image_path`、`receipt_path` | 相对交接根目录的文件路径 |
| `prompt_sha256`、`image_sha256`、`receipt_sha256` | 对应文件的 SHA-256 |
| `generation` | 真实结果的来源记录；未知模型版本写 `unknown` |
| `independent_assets` | 初始为空，表示本阶段未输出独立素材 |
| `qa` | 本轮实际检查状态，待执行项目写 `pending` |

下一模块先检查相对路径安全与文件哈希，再读取内容。哈希可检出文件变化；它不证明素材来源真实，也不代替页面审核。

下一模块将 `script.json` 作为文字依据，将 PNG 作为视觉参考。可编辑重建需另行检查标题、表格、流程节点等对象，以及需要单独保留的复杂图像。素材是否可以复用应根据实际文件与页面逐项判断。
