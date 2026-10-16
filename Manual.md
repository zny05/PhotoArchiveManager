# Photo Archive Manager 使用手册

本手册说明桌面界面中的比较阈值、结果预览、筛选、搜索、逐项决策和隔离操作。工具支持任意可访问目录，不要求目录位于 D: 或 F:。

## 0. 语言与发行版

GUI 默认以中文显示。窗口右上角的语言按钮可在中文与 English 间切换；切换只影响界面显示，JSONL 报告中的稳定操作值（如 `copy`、`quarantine`）保持不变。

Windows 便携版解压后运行 `PhotoArchiveManager.exe` 即可，不需要另装 Python。发行包已包含项目 Python 依赖、ExifTool、FFmpeg/ffprobe 和图标；请保留整个 `PhotoArchiveManager` 文件夹结构，不要单独移动 EXE。源码环境可运行 `build_windows.ps1` 重新构建；构建机需将 FFmpeg 的 `bin` 目录加入 PATH，或设置 `PHOTO_ARCHIVE_FFMPEG_BIN` 指向该目录。

## 1. 启动与依赖

在项目根目录运行：

```powershell
python -m pip install -r requirements-photo-backup.txt
python photo_backup_gui.py
```

Pillow、ImageHash 和 pillow-heif 用于静态图读取与 pHash。ExifTool 的 Python 封装已列入依赖，项目也在 `exiftool-13.59_64` 中携带了 ExifTool 13.59；程序会优先使用项目内的 `exiftool.exe`，再检查 `PHOTO_BACKUP_EXIFTOOL` 环境变量和系统 PATH。通常无需单独安装 ExifTool。若更新该目录，请保留同目录的 `exiftool_files`。`exiftool(-k).exe` 是交互/双击版，项目另有 `exiftool.exe` 命令行入口。

视频首帧预览使用系统 FFmpeg；请确保 `ffmpeg.exe` 可从 PATH 找到。GUI 在后台解码视频第一帧到内存，不会在媒体目录写入预览图。若未安装 FFmpeg，视频属性仍可显示，但预览画布会说明无法提取画面。

## 2. 选择扫描模式

### Compare source folder to target folder

用于把一个目录中的新文件备份到另一个目录，同时检查目标目录自身已有的重复项。`Source` 和 `Target` 可以是任意两个不互相包含的目录。源文件只会被读取；apply 的复制操作不会删除或移动源文件。

SHA-256 完全相同的文件会作为精确副本处理。视觉相似但哈希不同的照片会作为待审阅候选，不会因为 pHash 自动跳过或隔离。

### Find duplicates inside one folder

用于扫描单一目录内部的重复照片或视频。选择扫描目录后，Target 会锁定为同一目录。预览会跳过该目录下的隔离子目录，避免把已隔离文件重新纳入比较。

同目录模式下，只有 apply 并获准隔离的行会从原目录移入 Quarantine；扫描本身不会改动文件。隔离目录、报告和日志默认位于扫描目录上一级的 `Quarantine` 文件夹。

## 3. Preview options

### Find visual-similarity candidates (pHash)

开启后，工具会对 Pillow 能解码的图像计算感知哈希，并按哈希位差生成待人工检查的相似候选。图像解码失败、部分 RAW 格式或缺少对应解码插件时，文件仍可参与 SHA-256 精确比较和备份，但不会参与视觉比较。

关闭此项时，仍会扫描文件并计算 SHA-256，但不生成 pHash 候选。对十万级目录可关闭此项以减少图像解码工作量。终端会提示 `Visual comparison was turned off ...`；只有勾选了此项但缺少依赖时，才会提示安装 `requirements-photo-backup.txt`。载入旧报告不会改变此复选框，扫描前请确认它已勾选。

### pHash review distance (0-64)

pHash 使用 64 位图像指纹。此数值是两个指纹之间允许的最大汉明距离，也就是不同 bit 的数量；比较 64 个 bit 中有多少位不相同。

- `0`：只有 pHash 指纹完全相同才会列为候选。它仍不等于字节相同或图像内容相同。
- `4`：默认值；允许最多 4 位不同，通常是较严格的候选筛选起点。
- 更大的值：会列出更多候选，也更容易把不同照片列入人工审核。
- `64`：不建议用于日常整理，会使候选范围非常宽。

距离阈值控制“列出哪些候选”，不控制自动删除。任何 pHash 结果都不是照片相同的证明。阈值偏低可能漏掉裁切、旋转、编辑或强压缩版本；阈值偏高会增加误报。建议先用 `4` 或更低值试跑，检查候选后再调整。

### Read Live Photo ContentIdentifier (ExifTool)

开启后尝试读取照片和视频中的 `ContentIdentifier`，将识别到的静态图与动态文件关系作为报告证据。它不会自动配对、复制、移动或删除文件，也不会仅凭文件名配对。项目会优先调用随项目携带的 ExifTool。

## 4. 结果列表和图片预览

预览完成后，右侧表格显示决策、操作类型、文件、匹配文件和 pHash 距离。选择一行后，右侧下方会并排显示该行的图片与匹配图片，并显示两边的文件属性。

属性面板优先显示本地可直接读取的文件名、类型、文件大小、修改时间；ExifTool 可用时还会显示 MIME、拍摄时间、相机厂商/型号、图像尺寸、视频时长/压缩器、方向及 ContentIdentifier（文件中存在时）。图像和视频分辨率由程序直接读取文件头得到，不依赖 ExifTool：图片用 Pillow 读取像素宽高（HEIC 需 pillow-heif，并按 EXIF 方向调整），视频用 ffprobe 读取首个视频流（含旋转标记；没有 ffprobe 时回退到 ffmpeg 的输出），都失败时才使用 ExifTool 的宽高标签。显示为 `Resolution: 4032 x 3024 px (12.2 MP)`；读取失败显示 `Unavailable`。含中文、日文或 emoji 的路径也可正常显示。修改时间按文件时间戳转换为 UTC+08:00，带时区的拍摄时间转换到 UTC+08:00，没有时区的相机时间按 UTC+08:00 原始时间显示。无效的零日期不会显示。ExifTool 读取在后台执行，快速切换时旧请求会被忽略，重复查看未变化文件会复用缓存。选择视频行时，画布显示其首帧；RAW、HEIC 等格式能否预览取决于 Pillow 和相应解码插件。遇到 FFmpeg 不可用、不支持的格式、损坏文件或文件在预览后被移走时，画布会显示无法预览的原因。预览图片只是方便人工比对，不改变扫描报告中的 SHA-256 或 pHash 结果。

GUI 启动时会尝试最大化。左右“目录/操作设置”和“结果工作区”之间，以及右侧上下“结果列表”和“详情/预览属性”之间，都有可拖拽的分隔条。拖动分隔条可按当前屏幕空间调整区域；若窗口处于非最大化状态，可先最大化窗口再调整。

`Selected entry` 显示完整路径、匹配路径和原因。对于 Live Photo 信息行，会列出静态图和动态文件路径；当前预览会显示静态图及可用的视频首帧，不播放 MOV/MP4。

## 5. Filter 下拉列表

Filter 只影响表格显示，不会改变报告决策或扫描结果。

| 选项 | 用途 |
| --- | --- |
| `All entries` | 显示报告中的所有项目。 |
| `Needs review` | 只显示 `decision` 仍为 `pending` 的项目。应用计划前应清空此筛选并确认所有待审项目已处理。 |
| `copy` | 显示计划从 Source 复制到 Target 的文件。 |
| `skip` | 显示因 SHA-256 已有精确副本而跳过的文件。 |
| `move_exact` | 显示目标目录中常见静态图的字节级重复项；默认决策为 `quarantine`，apply 前仍需授权。 |
| `review_target_exact` | 显示目标中的精确重复视频、RAW、TIFF 或 sidecar；默认待审，由人工决定隔离或保留。 |
| `review_source_visual` | 显示 Source 中与 Source/Target 候选视觉相似的文件；可决定继续复制或跳过，不会从 Source 隔离。 |
| `review_target_visual` | 显示 Target 或单目录扫描中视觉相似的图片候选；可决定隔离该行文件或保留。 |
| `collision` | 显示目标相对路径已有不同内容、因此拒绝覆盖的项目。该类型只能保留并人工处理路径冲突。 |
| `live_pair_info` | 显示 ExifTool 根据 ContentIdentifier 找到的静态图/动态文件关系，仅供参考，不执行文件操作。 |

表格最多每页显示 400 条；使用 `Previous` / `Next` 翻页。当前筛选和搜索会共同生效。

## 6. Search paths / notes

在搜索框输入任意文本即可实时筛选当前报告。搜索范围包括文件路径、匹配路径、原因、操作类型、当前决策，以及可用时的 ContentIdentifier、Live Photo 静态图路径和动态文件路径。

搜索不改变文件，也不修改决策。清空搜索框即可恢复当前 Filter 下的完整结果。

## 7. Selected decision

1. 在表格中选择一行。
2. `Selected decision` 下拉框会显示该操作允许的决定。
3. 选择决定并按 `Set`。这一步只修改内存中的报告，不会立即复制或移动任何文件。
4. 按 `Save decisions` 保存。也可以在 apply 前由界面自动保存。

各类行可选项如下：

| 操作类型 | 可选决策 | 说明 |
| --- | --- | --- |
| `copy` | `copy` / `skip` | 是否复制新文件到 Target。 |
| `review_source_visual` | `copy` / `skip` | 视觉候选是否继续备份；不对 Source 做隔离。 |
| `move_exact` | `quarantine` / `keep` | 精确重复静态图是否移入隔离区。 |
| `review_target_exact` | `quarantine` / `keep` | 视频、RAW、TIFF 或 sidecar 精确重复是否移入隔离区。 |
| `review_target_visual` | `quarantine` / `keep` | 目标中的视觉相似候选是否移入隔离区。 |
| `collision` | `keep` | 文件内容不同但目标路径冲突；工具不会覆盖。 |
| `skip` | `skip` | 精确副本已存在，不需要再次复制。 |
| `live_pair_info` | `keep` | 只是一条元数据关系记录，不执行文件操作。 |

报告中仍有 `pending` 决策时，Apply 不可执行。先在 Filter 中选择 `Needs review`，逐项选择后按 `Set`，再保存并应用。

### 批量决策（几千至几万条）

表格下方的 `Bulk decision for ALL filtered entries` 会把所选决策一次应用到**当前 Filter 与 Search 结果的所有页**，不只是当前页：

1. 用 `Filter`（如 `review_target_visual`、`move_exact`）和 `Search paths / notes`（如某个子目录名）缩小范围。
2. 在 Bulk 下拉框选择 `copy`、`skip`、`quarantine` 或 `keep`。
3. `Only pending` 勾选时只改仍为 `pending` 的行，不覆盖已手动处理的行；取消则覆盖范围内所有行。
4. 点 `Set all filtered`，确认弹窗会显示将修改的数量、筛选范围和因不兼容而跳过的数量（例如把 `quarantine` 应用到 `review_source_visual` 会被跳过，因为该类型只允许 `copy`/`skip`）。
5. 点 `Undo last bulk` 可撤销最近一次批量修改；载入新报告后撤销记录清空。

批量设置只修改内存中的报告，不移动文件。完成后 `Save decisions`，再勾选 Apply safeguards 并应用。建议先批量处理一个筛选，再抽查个别行的并排预览。

隔离结果可在 Quarantine 文件夹中人工复核：隔离文件保留原相对路径，Action log 记录每项来源与目的地；确认误判时将文件移回原路径即可。

## 8. Apply safeguards 四项授权

这四个勾选项是 apply 的额外授权，不会替代表格中的逐项决策，也不会单独启动操作。修改前应确认 Source、Target、Quarantine、Report 和 Action log 路径正确。

### Allow quarantine moves (master permission)

隔离操作的总开关。只要计划中有任一行决定为 `quarantine`，必须勾选此项。它不会自动选择要隔离的文件；必须先在报告行中将相应决策设为 `quarantine`。

### Allow reviewed visual-match quarantine

仅额外授权 `review_target_visual` 的视觉相似候选进入隔离区。必须同时满足：该行决策为 `quarantine`、勾选总隔离授权和此项、最后勾选确认。它不适用于 `review_source_visual`；Source 侧视觉候选只能选择复制或跳过。

### Allow reviewed video quarantine

仅额外授权视频文件进入隔离区。视频精确重复默认待人工处理；需要先将对应行设为 `quarantine`，再同时勾选总隔离授权和此项。工具只处理被选中的视频文件，不会自动连带移动配对静态图。Live Photo 关系不明确或仍需保留动态部分时，应选 `keep`。

### Confirm filesystem changes

所有实际复制或隔离操作都必须勾选此项，并在点击 `Apply reviewed plan` 后确认弹窗。没有此确认，apply 会拒绝执行。复制会保留 Source 原文件；隔离会从原目录移出文件，因此只能在检查报告并确定恢复路径后授权。

## 9. 正确执行隔离

如果只点击 `Create preview`，只会写 JSONL 报告，不会移动任何照片。看到 `move_exact` 或其他候选不代表它已经进隔离区。

### 精确重复静态图

1. 选择正确的备份模式或单目录模式，确认 Source/Target 和 Quarantine 路径。
2. 点击 `Create preview`，等待扫描完成。
3. 选择 `move_exact` Filter，检查每个待隔离行的原路径、匹配路径和决策；不应隔离的行改为 `keep`。
4. 勾选 `Allow quarantine moves (master permission)` 和 `Confirm filesystem changes`。
5. 点击 `Apply reviewed plan`，在确认弹窗选择继续。
6. 检查完成提示中的 Action log，再检查 Quarantine 目录和原目录。

### 视觉相似候选

`review_target_visual` 默认是 `pending`。先在 `Needs review` 中逐条查看并排图片，把确认冗余的行设为 `quarantine`，确认为不同照片或编辑版本的设为 `keep`。应用时勾选总隔离授权、`Allow reviewed visual-match quarantine` 和确认项，再点击 Apply 并确认。

`review_source_visual` 是 Source 对比候选，只能选 `copy` 或 `skip`。决定复制时会把 Source 文件复制到 Target，不会移动或隔离 Source 文件。

### 视频、RAW、TIFF 和 sidecar

这些类型即使 SHA-256 完全相同，也先列为 `review_target_exact` 待审。要隔离时，逐项设为 `quarantine`；勾选总隔离授权。若文件是视频，还要勾选 `Allow reviewed video quarantine`。逐项确认 Live Photo 静态图/动态文件关系；工具不会自动把一对文件作为整体移动。

隔离通过同卷重命名（移动）完成，不需要硬链接，因此 exFAT/FAT32 备份盘也可用；Quarantine 必须与被整理目录位于同一文件系统/卷，跨卷时 apply 会在读取任何文件之前拒绝。工具不会永久删除隔离文件；恢复时将文件从 Quarantine 移回原相对路径，并核对 Action log。

## 10. 常见“没有移动文件”的原因

点击 `Apply reviewed plan` 后，程序直接按报告中已 Set 的决策执行：标为 `quarantine` 的文件移到隔离文件夹，标为 `copy` 的文件复制到 Target，其余不动。不会重新读取、哈希或校验任何媒体文件，因此预览之后被你修改过的文件不会被检测到；预览与 Apply 之间请不要改动目录内容。界面先短暂显示 `Preparing plan`（只检查路径、授权、目标是否冲突和是否会隔离同一组的全部副本，不读文件内容），再显示 `Applying changes`（实际复制或移动）。结束后弹窗会列出复制数、移入隔离区的数量和隔离文件夹的完整路径，也可点击左侧 `Open quarantine folder` 直接打开。

- 只生成了预览，尚未执行 Apply。
- 看错了文件夹：默认隔离文件夹现在是被整理目录上一级的 `Quarantine`（旧版本默认是 `PhotoQuarantine`），以弹窗和 `Quarantine folder` 栏显示的路径为准。
- 弹窗显示 `moved to quarantine 0`：没有任何行的决策是 `quarantine`（可能全是 `keep`、`skip`、`copy`），弹窗会提示这一点。
- 有 `pending` 项未处理，Apply 会被禁用或拒绝。
- 表格行的决策是 `keep`、`skip` 或 `copy`，没有任何隔离操作。
- 没有勾选 `Allow quarantine moves (master permission)`。
- 视觉候选没有同时勾选 visual-match 授权；视频候选没有同时勾选 video 授权。
- 没有勾选 `Confirm filesystem changes`，或确认弹窗选择取消。
- Quarantine 路径不在允许范围内、已被占用，或与目标不在同一文件系统。
- Apply 检查到目标冲突（目标路径已存在、同一目的地被多条记录使用）或会隔离同一组的全部副本，于是在移动任何文件之前中止。
- 所选行根本不是重复项，例如 `collision`；该类型不可隔离，工具会避免覆盖不同内容。

## 11. 默认输出位置、进度条与保留偏好

### 默认输出位置

选择被整理目录后（单目录模式是扫描目录，备份模式是 Target），`Quarantine folder`、`Review report` 和 `Action log` 默认放在该目录**上一级**的 `Quarantine` 文件夹中，例如扫描 `E:\Photos` 时：`E:\Quarantine\`、`E:\Quarantine\review-时间戳.jsonl`、`E:\Quarantine\actions-时间戳.jsonl`。手动修改过的路径不会被覆盖；报告或日志文件名已存在时会自动换新时间戳。扫描根目录本身是盘符根（如 `D:\`）时没有上一级，请手动选择位置。

### 进度条

表格下方有两个进度条。`Preview` 显示 `Create preview` 的 5 个阶段：扫描文件夹、读取 Live Photo 标识、哈希 Target、哈希 Source、图像相似比较，并显示已处理数量和百分比。`Apply / auto-pick` 显示 Apply 的 `Preparing plan` 与 `Applying changes`，以及保留偏好自动选择的进度。

### Selected entry

选中一行后，`Selected entry` 显示：动作、当前决策、pHash 距离、Apply 将对该行做什么（移动到隔离区、复制、不处理或尚未决定）、隔离目标路径、选中文件与匹配文件的完整路径、两者的 SHA-256（并标明字节是否相同）、以及原因。Live Photo 行显示 ContentIdentifier 与静态图/动态文件路径。

### 保留偏好（Keep preference / auto-pick）

点击表格下方的 `Keep preference / auto-pick...`，对**当前 Filter 与 Search 结果的所有页**自动选择要保留的文件，并把其余文件标为隔离。最多设置 4 级条件，前面的条件优先，后面的只在前面相同时决胜，最后仍相同时按路径名决定：

| 条件 | 含义 |
| --- | --- |
| Preferred file type | 按“File type order”从前到后优先，默认 `.dng, .heic, .heif, .tiff, .tif, .png, .jpg, .jpeg, .webp`。 |
| Higher resolution | 像素总数更多者优先。 |
| Larger / Smaller file size | 文件更大 / 更小者优先。 |
| Newer / Older creation time | 创建时间更新 / 更早者优先。 |

默认顺序是：文件类型、分辨率、文件大小、创建时间（更早）。规则细节：

- 精确重复（`move_exact`、`review_target_exact`）：每组只保留最优的一个，其余全部标为 `quarantine`。
- 视觉相似（`review_target_visual`）：每对保留较优者，较差者标为 `quarantine`。如果较优者在另一对里被判为较差而要被移除，这一对会保持原样留给人工复核，并在完成后的统计里显示 `left for manual review`。
- `review_source_visual`：Source 文件更优则 `copy`，匹配文件更优则 `skip`。
- `Only pending` 勾选时只处理仍是 `pending` 的行；精确重复的默认决策不是 `pending`，要重新按偏好选择时请取消勾选。
- 结果只修改内存中的报告，可用 `Undo last bulk` 撤销；之后仍需 `Save decisions`、勾选授权并 Apply。
- Apply 还有一道保护：任何会让同一组内所有副本都被隔离的方案都会被拒绝。

## 12. 报告与日志

Preview report 保存扫描结果和逐项决策。Apply 会要求新的 Action log 路径，并为每项成功复制或隔离的文件记录来源、目的地、SHA-256 和时间。报告与日志应放在媒体目录之外，并保留一份用于追踪和恢复。

十万级目录每次预览会重新哈希支持的文件，建议先选小目录验证阈值、结果和隔离路径，再分批处理。不要以相同文件名、拍摄时间或单独的 pHash 结果作为移动依据。