# 知行版本主题图生成记录

## tebb v0.4.1：让对话，更清晰

使用内置 imagegen 生成并检查文字与范围。暖白纸张、低饱和铅笔漫画延续圆形 tebb 与狐狸、猫、猫头鹰伙伴；三个场景对应统一新对话入口、连续回复与明确连接飞书。图中树与气泡是功能概念插画，不代替实际界面截图，不承诺模型性能。原始提示词见 [v0.4.1-poster-prompt.txt](releases/v0.4.1-poster-prompt.txt)，素材为 desk_companion/ui/release-v0.4.1.png，用于候选版本介绍和通知卡片无副作用预览；生成图片不代表已经正式发布或发送通知。

## tebb v0.4.0：把计划，变成成果

使用内置 imagegen 生成并检查文字、功能范围与构图。低饱和铅笔漫画延续圆形 tebb、狐狸、猫和猫头鹰伙伴，四个场景对应事项日程、伙伴自动化、搜索图表与上下文记忆。主题插画不作为实际界面截图或性能承诺。原始提示词见 [v0.4.0-poster-prompt.txt](releases/v0.4.0-poster-prompt.txt)，正式素材为 `desk_companion/ui/release-v0.4.0.png`，用于 README、版本介绍和发布卡片。

## v0.2.3：彩色更新亮点

用户确认彩色手绘预览后，使用内置 image_gen 仅移除底部的预览/未打包标记，正式图片保存为 `desk_companion/ui/release-v0.2.3.png`。保留用户确认的配色、人物和六项功能介绍；中间突出视频学习、Agent 调用透明度和资料管理。

完整提示词见 [彩色编辑提示词](releases/v0.2.3-preview-color-prompt.txt) 与 [正式版定稿提示词](releases/v0.2.3-final-prompt.txt)。正式图用于版本介绍与飞书发布卡片。

## v0.2.2 知行铅笔卡通主题图

使用内置 image_gen：先从上一版漫画图重绘铅笔绘基底，再编辑为 Q 版凯尔希并放大版本号。最终图片已由用户审阅确认，保存于 desk_companion/ui/release-v0.2.2.png，版本介绍与飞书卡片共用。

采用低饱和石墨灰与纸张底色、交叉排线、密集的功能流程和本版更新面板，品牌为知行 · ZhiXing，文案为「从对话，到行动」。

## 铅笔绘基底提示词

Redraw the supplied ZhiXing comic cover into a detailed GRAPHITE PENCIL DRAWING on natural ivory sketchbook paper. This is a medium/style redesign and a denser information layout. Use the supplied image only to retain the recognizable adult Kal'tsit character and the exact product identity 知行 / ZhiXing; do not retain its bright colors or flat ink blocks. It must visibly look hand-drawn in pencil: thin and varied graphite strokes, layered cross-hatching, soft smudged graphite shading, faint construction lines, sketchy margins, subtle eraser marks, fine paper tooth. Predominantly monochrome graphite gray on slightly warm off-white paper. NO bright emerald green, NO saturated accent colors, NO glossy digital rendering, NO neon, NO solid heavy black ink, NO brush calligraphy poster look. At most a tiny touch of desaturated gray-green in her eyes; all other content grayscale. Landscape 16:9, premium dense editorial pencil storyboard / an inventor's annotated sketchbook page, beautiful precise drawings rather than rough scribble. Character: Kal'tsit from Arknights, silver-white hair, feline ears, composed intelligent adult woman in white medical jacket and modest technical clothing, working at a desk. Anatomically natural small hands, five digits each when visible, no foreshortened hand pointing at camera. Character/desk vignette occupies the right-middle 35%, leaving room for meaningful feature diagrams. No unrelated decorative cat vignette. Left-upper branding EXACT '知行' and below EXACT 'ZhiXing'; subtitle EXACT '个人 Agent 工作台'. Main headline EXACT '从对话，到行动' in carefully penciled block lettering, not huge solid black paint. Small top-right version badge EXACT 'v0.2.2'. Increase information density using eight readable pencil-drawn functional mini-panels arranged around the workspace with hand-drawn arrows and ruled labels. A connected workflow diagram shows: speech bubble label EXACT '对话' → notebook label EXACT '记忆' → wrench and command-sheet label EXACT '工具与技能' → calendar/checklist label EXACT '任务调度' → chat/doc vignette label EXACT '飞书协作'. Another clearly labeled panel EXACT '聊天监控' shows task progress with only a few drawn lines, no invented tiny text. In the bottom strip under the exact section label '本版更新', show four compact meaningful update panels with exact labels '版本介绍', '检查更新', '发布通知', '5条技术资讯'; first panel is a release booklet, second an update arrow, third an image-message card, fourth a stack of source pages. This is an illustrated feature map for real app capabilities, not a fake screenshot. All these labels must be accurately spelled, clearly readable and not tiny. No extra text, no pseudo-English, no fake code, no logos, no watermark, no slogans about upgrading. Overall density should come from relevant diagrams, small objects, pencil annotations, connectors and hatching, not ornamental clutter. Clear hierarchy and comfortable breathing room despite dense content. 4% safe margin, no clipped letters or face, opaque paper background.

## 最终编辑提示词

Edit the supplied ZhiXing pencil cover with TWO targeted changes, preserving its landscape 16:9 graphite-on-warm-paper style, dense feature map, Chinese wording, functional diagrams, and overall composition.
1. Replace ONLY the adult realistically proportioned anime Kal'tsit at the right desk with a clearly CARTOON / CHIBI Kal'tsit mascot. Make her approximately 2.5–3-head-tall proportions, a large cute round head, simplified expressive big eyes, small nose and mouth, petite body, short simplified arms, unmistakable silver-white hair and feline ears, white medical coat and dark technical clothes. Keep her calm, smart, slightly deadpan personality rather than a silly grin. She sits at the same workstation as a cute serious little scientist, with a pencil or notebook. Draw the character in graphite pencil with visible sketch strokes and soft cross-hatching, not full-color anime, not a realistic adult face or body. Simple anatomically correct small cartoon hands; do not add extra fingers. Maintain a tiny subdued gray-green tint in the eyes only if needed, otherwise monochrome. Adapt the surrounding right-side workstation naturally to her smaller cartoon proportions while retaining information density. Avoid covering diagram labels.
2. Make the main version number EXACT 'v0.2.2' MUCH MORE PROMINENT. Replace the small top-right tilted tag with a large, clean hand-drawn rectangular release badge occupying roughly the top-right 20% width and 16% height. Its graphite lettering should be at least twice the previous height, high-contrast dark pencil with a lightly hatched border and generous blank paper padding. It must be readable immediately at thumbnail size and be one of the two strongest focal elements alongside the brand '知行'. Position it above the cartoon character without overlapping her ears, face, labels or edge. Keep the subtle small version in the booklet if already present, but do not add other duplicate version badges.
Preserve accurately spelled text: '知行', 'ZhiXing', '个人 Agent 工作台', '从对话，到行动', '对话', '记忆', '工具与技能', '任务调度', '飞书协作', '聊天监控', '本版更新', '版本介绍', '检查更新', '发布通知', '5条技术资讯'. No new slogans or additional fake text. Retain the bottom four feature panels and upper connected workflow diagrams, pencil construction lines, cross-hatching, natural paper tooth and restrained low-saturation palette. No bright colors, heavy solid ink blocks, glossy digital rendering, neon effects or watermarks. Keep all important content inside safe margins. This is a review preview, not a UI screenshot.


## tebb v0.3.0

用户已确认圆形小伴与伙伴小队、低饱和铅笔漫画跨页，以及最终生成图。使用内置 image_gen 生成，未对图像作额外编辑；原始提示词见 [v0.3.0-concept-prompt.txt](releases/v0.3.0-concept-prompt.txt)。最终素材为 `desk_companion/ui/release-v0.3.0.png`，用于 README、版本介绍与正式发布卡片。
