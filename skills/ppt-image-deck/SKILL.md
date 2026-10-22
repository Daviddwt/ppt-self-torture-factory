---
name: ppt-image-deck
description: Create image PPTX decks in Codex. Keep PNGs, prompts and handoff records.
---

# 制作图片 PPT

本技能用于 Codex，将已确认的逐页脚本制作成图片 PPT。交付包括整页 PNG、图片 PPTX、逐页提示词和交接清单。文字与图形在本阶段保存在整页图片中。

## 开始前确认输入和工具

读取用户的工作要求，确认脚本已通过内容审核。输入使用模块一的 `script.json`。

格式见 [输入与交接约定](references.md)。[示例](examples/script.json) 为虚构培训材料。

确认本次 Codex 对话可以调用内置图片生成工具。脚本只准备提示词、保存真实生图结果和组装文件。内置生图未接入时，说明当前无法调用并停止生图；更换路线需用户确认。

组装使用已安装且获授权的 `@oai/artifact-tool`。使用者先核实使用及再分发限制。包内仅含新写的调用代码，运行库需自行提供；脚本不安装依赖。依赖缺失时停下并报告。

## 执行流程

以下命令从本技能目录执行。先创建一个存放工作结果的父目录，再为三个阶段分别选用尚不存在的目录。命令中的路径为示例，用实际路径替换。

1. 准备提示词。脚本保留输入 JSON 的原始字节，并为每页生成提示词和 SHA-256 校验值。

   ```bash
   node scripts/prepare_prompts.mjs \
     --script /path/to/script.json \
     --out /path/to/work/image-input
   ```

2. 逐页调用 Codex 内置图片生成工具。将对应 `prompts/<id>.md` 作为提示词，要求完整页面、指定画幅和不透明背景。调用前读取工具当前说明，使用其支持的参数。已有参考图须按用户授权范围提供。将工具真实返回的 PNG 保存到可读取的位置。

3. 登记每页图片。`--source-origin` 记录结果来源。`--tool-result-ref` 记录工具调用或结果编号。实际模型版本可取得时填写；工具未暴露版本时保留 `unknown`。本命令按原始字节复制图片，不裁切、不重采样，也不覆盖输入。

   ```bash
   node scripts/record_image.mjs \
     --workspace /path/to/work/image-input \
     --id P01 --image /path/to/generated/P01.png \
     --source-origin 'Codex image generation result' \
     --tool-result-ref 'actual-result-reference' \
     --model unknown
   ```

4. 逐页查看真实 PNG，对照脚本检查文字、事实、布局和清晰度。生图中的文字可能有误。发现问题时保留该轮文件，在新目录生成修订轮次。图片宽高比与画布相差超过 0.5% 时，组装器会拒绝继续。

5. 组装图片 PPT。脚本优先从当前工作目录的依赖解析规则查找运行库，也可指定已有模块入口。确认授权后添加 `--runtime-authorized yes`。

   ```bash
   node scripts/assemble_deck.mjs \
     --workspace /path/to/work/image-input \
     --out /path/to/work/image-build \
     --runtime-authorized yes
   ```

   指定入口使用 `--artifact-module` 参数，值为已授权运行库的模块路径。本命令会执行所指定的模块，路径应指向使用者信任的本地运行库。

6. 检查实际 PPTX 的页面数量、顺序及图片显示。运行可用的 OOXML 检查工具，并在 PowerPoint 中打开文件做最终或小样本检查。桌面能力或文件授权受阻时停止重试。记录状态为 `PowerPoint GUI validation pending`。每页独立 PNG 和图片 PPT 都需要视觉检查。脚本产生的 `qa` 字段只记录已实际执行的程序检查，其他项目初始为 `pending`。

7. 生成独立交接目录。交接文件保留输入、图片、提示词、来源记录、PPTX 以及文件校验值。

   ```bash
   node scripts/create_handoff.mjs \
     --workspace /path/to/work/image-input \
     --build /path/to/work/image-build \
     --out /path/to/work/image-handoff
   ```

   另附本轮审核记录，写明逐页结果、检查人或检查工具、时间和对应文件哈希。交接包初始状态为 `review_required`。下一模块读取证据后独立决定是否接收。

## 交付边界

- PPTX 每页只放一张完整图片，可编辑重建由下一模块完成。
- 本技能提供整页 PNG。独立插画、透明 PNG 和 SVG 图标需另行取得并登记来源；交接清单中 `independent_assets` 初始为空。
- 生成素材、原始素材和后续提取素材分别记录来源。已有图片中的局部元素需经过单独处理和验证，才能进入独立素材清单。
- 脚本保留 `source_refs`，额外业务字段也随 `script.json` 原样保留。来源内容缺失或脚本与图片冲突时，先向用户确认。
- 所有输出均防覆盖。失败返回非零退出码；已产生的部分文件保留，目录级流程写入 `FAILED.json`。再次运行使用新目录，不删除原始资料。

## 开发验证

```bash
node --test tests/workflow.test.mjs
```

测试在临时目录使用明确标注的合成数据，验证输入和文件保护逻辑。测试不联网、不调用真实生图模型、不导入或执行私有运行库。测试通过后仍需在接收者环境验证真实生图、实际运行库导出和 PowerPoint 打开结果。
