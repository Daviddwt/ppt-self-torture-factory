import fs from 'node:fs/promises';
import path from 'node:path';
import { cliArgs, collectImages, exclusiveWrite, failureMarker, isMain, jsonBytes, loadWorkspace, newDirectory, portableRelative, readInside, runCli, sha256 } from './common.mjs';

export async function createHandoff(options) {
  const workspace = await loadWorkspace(options.workspace);
  const images = await collectImages(workspace);
  const buildRoot = await fs.realpath(path.resolve(options.build));
  const buildBytes = await readInside(buildRoot, 'build-manifest.json');
  const build = JSON.parse(buildBytes);
  if (build.schema_version !== '1.0' || build.kind !== 'assembled_image_deck' || build.script_sha256 !== workspace.manifest.script_sha256) throw new Error('build manifest does not match prepared input');
  portableRelative(build.deck_path);
  if (build.deck_path !== 'deck.pptx') throw new Error('unexpected deck path');
  const deckBytes = await readInside(buildRoot, build.deck_path);
  if (sha256(deckBytes) !== build.deck_sha256) throw new Error('PPTX hash mismatch');
  if (!Array.isArray(build.slides) || build.slides.length !== images.length) throw new Error('build slide count mismatch');
  for (let i = 0; i < images.length; i++) {
    for (const key of ['id', 'image_sha256', 'prompt_sha256', 'receipt_sha256']) {
      if (build.slides[i][key] !== images[i][key]) throw new Error(`build ${key} mismatch at slide ${i + 1}`);
    }
  }
  let output;
  try {
    output = await newDirectory(options.out, [workspace.root, buildRoot]);
    for (const dir of ['prompts', 'assets', 'receipts']) await fs.mkdir(path.join(output, dir));
    await exclusiveWrite(path.join(output, 'script.json'), workspace.scriptBytes);
    await exclusiveWrite(path.join(output, 'deck.pptx'), deckBytes);
    await exclusiveWrite(path.join(output, 'build-manifest.json'), buildBytes);
    const slides = [];
    for (let i = 0; i < images.length; i++) {
      const image = images[i];
      for (const key of ['prompt_path', 'image_path', 'receipt_path']) {
        const bytes = await readInside(workspace.root, image[key]);
        if (sha256(bytes) !== image[key.replace('_path', '_sha256')]) throw new Error(`input changed during handoff: ${image[key]}`);
        await exclusiveWrite(path.join(output, image[key]), bytes);
      }
      const { bytes: _bytes, ...metadata } = image;
      slides.push({ ...workspace.script.slides[i], ...metadata });
    }
    const manifest = {
      schema_version: '1.0', kind: 'image_ppt_to_editable', status: 'review_required',
      title: workspace.script.title, canvas: workspace.script.canvas,
      script_path: 'script.json', script_sha256: workspace.manifest.script_sha256,
      deck_path: 'deck.pptx', deck_sha256: build.deck_sha256,
      build_manifest_path: 'build-manifest.json', build_manifest_sha256: sha256(buildBytes),
      slides, independent_assets: [],
      capabilities: { full_slide_png: true, editable_pptx: false, automatic_transparent_illustrations: false, automatic_svg_icons: false },
      qa: build.qa,
      rebuild_rules: [
        'Use script.json and prompts for approved text; use PNG pages for visual reference.',
        'Keep the page order, meaning and source_refs. Resolve conflicts with the user.',
        'Assess editable reconstruction per page before accepting the handoff.',
        'Record newly produced or separately sourced assets with their actual provenance.',
      ],
    };
    await exclusiveWrite(path.join(output, 'manifest.json'), jsonBytes(manifest));
    await exclusiveWrite(path.join(output, 'HANDOFF.md'), [
      '# 图片 PPT 交接说明', '',
      `本包含 ${slides.length} 页图片 PPT、逐页 PNG、提示词和来源记录。当前状态为审核稿。`, '',
      '下一阶段先读取 script.json 和 manifest.json，再逐页核对文字及版式。文字以 script.json 为准，PNG 提供视觉参考。', '',
      '独立插画、透明 PNG 和 SVG 图标需单独取得并登记来源。本包的 independent_assets 为空。', '',
      'build-manifest.json 保留组装检查状态。PNG 视觉审核、PPTX 结构审核及 PowerPoint 实机验证由实际执行者完成后另附证据。', '',
      '审核记录应写入新文件，保留本包原件。审核通过后再决定进入可编辑重建。', '',
    ].join('\n'));
    return path.join(output, 'manifest.json');
  } catch (error) { await failureMarker(output, error); throw error; }
}
if (isMain(import.meta.url)) await runCli(() => createHandoff(cliArgs(process.argv.slice(2), ['workspace', 'build', 'out'], ['workspace', 'build', 'out'])));
