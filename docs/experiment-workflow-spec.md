# 五瓶双水相实验工作流（2026-11-14）

## 已确定的实验条件

自动实验使用 1—5 号瓶，从画面右向左编号；6 口保留手动调试。每个盐分别研究 PEG＋盐＋水，默认总质量 1.00 g，25 °C。10 口为 30% 柠檬酸三钠，11 口 40% 硫酸铵，12 口 15% 硫酸钠，13 口 **40% 磷酸氢二钾 K₂HPO₄**，14 口 40% PEG4000，15 口水。百分数全部为 w/w；密度未测。来源硬管均 80 cm，样品管 50 cm，A/B/C 为 10/10/71 cm。泵1、阀2共线，沿用 CH340 扫描、CCW抽吸、CW追送。

## 交付范围与原则

保留 Python 本地服务、原有手动操作和桌面 EXE 外壳，新增六页导航与常驻状态栏。实验信息使用独立 SQLite 数据库，旧数据不改写。软件开发测试只使用测试替身，不连接仪器。全部完成验证后才提交、推送仓库。

质量组成先换算为母液质量：PEG母液=M×wPEG/cPEG，盐母液=M×wSalt/cSalt，补水=M−前两项。负补水拒绝。缺密度仍可预览质量，但执行需要每个来源自己的样品端质量交付标定；不可套用清水0.15 mL。也可使用有实测依据的密度与该来源体积交付标定换算质量。标定保存原始称重、每组剂数、每剂吸取步数、追送步数、转速、批次、温度和管路签名。有限步数按四舍五入换算，量化后估计组成单独记录，不称为实际组成。

自动配液只在标定实测的单段步数以内分段；有密度时进一步要求每段估计不超过0.8 mL。未测密度时绝不把质量当体积，容量检查采用用户明确录入的该条件安全总质量上限；未填写则不允许自动启动。该上限是操作条件，不是软件证明瓶子容量。默认1 g配样可预览，安全质量上限默认空。

整瓶流程：逐来源吸取→反向追送（来源之间不清洗、不吹扫）→一次混合→16口向废液吹扫→独立静置。保留清洗方法和交叉污染未验证的说明。单总线串行，等待/成像不阻塞下一瓶配液。瓶位占用持续到人确认已换干净空瓶；管路排液不代表瓶子清洗。

停止保留连接和已完成动作；中断样品不得自动重做。重启恢复记录但自动运行关闭；运行中的样品标为中断。手动调试保留，手动液路动作暂停后续自动任务，触及占用瓶时记录扰动并取消旧拍照时间；单纯切阀不视为混合。软件停止与硬件急停分别标识。

## 图像、复核、模型

浏览器相机显式打开，保存五瓶ROI映射版本；自动拍照要求相机在线且映射一致。浏览器关闭/相机断开时记录未拍摄，重试只补图不补配液。照片关联不可变样品ID、瓶位、混合结束时间、实际采集时间、静置秒数、原图与ROI。默认拍照时间60/180/300/600 s、最早训练复核300 s、观察上限1800 s均是可修改工程设置，不是经验证的分相时间。重新加料/混合要更新状态与计时，旧照片保留但不冒充新状态。

只用人工确认、条件兼容、充分等待、无中断和无排除原因的数据训练。没有界面始终只是候选。待确认、反光/气泡/浑浊等保留原因。第一版采用无额外依赖的 RBF 距离加权分类和覆盖度选点；输出是启发式概率分数与不确定性分数，不是经校准置信区间或物理相边界。只有一类标签时推荐均匀覆盖点，不绘制可信两类边界。各盐独立，保存每轮训练ID和参数、候选点及理由，人工选择后加入队列；不自动连续运行。均匀取点比较仅使用实际试验数量/覆盖度/标签统计，不虚构准确率。照片不能提供两相化学组成或系线。

## 代码分工与接口约定

### 领域模块 `experiment_domain.py`

纯函数，不访问硬件或数据库：

- `default_stocks()` 返回 source端口为整数的列表，字段 `port,name,solute,mass_fraction,batch,temperature_c,density_g_ml,density_evidence,version`。
- `validate_stock(data)` 返回规范化记录；version可由调用方赋值。
- `make_calibration(data, stock, geometry_signature)`：输入 `source,groups_g,doses_per_group,aspirate_steps,aspirate_rpm,chase_steps,chase_rpm,note`，可选 `max_segment_steps` 不能大于实测 aspirate_steps。返回id、mass_per_step_g、原始证据、stock_version、geometry_signature、转速等；质量分组RSD不称为单剂RSD。
- `preview_recipe(request, stocks, calibrations, geometry_signature)`：request含 `salt_port,peg_fraction,salt_fraction,total_mass_g,temperature_c,safe_mass_limit_g,mixing`，fraction 0—1；stocks/calibrations均列表。返回 `target,stock_masses,executable,issues,estimated,plan`；plan兼容 FreeDebugMixin._execute_recipe 的结构（sample默认1，服务重新绑定）；缺标定仍返回可预览质量及issues，非法组成抛ValueError。来源次序PEG14、所选盐、补水15（零质量跳过）。必须所有有限动作满足controller.pulse_checked并验证超时覆盖时长，最大60段。
- `suggest_points(samples, request)`：输入已经由服务筛出的有效样品列表，每项含 `id,peg_fraction,salt_fraction,label`；request含盐、范围、步长、count≤5、method active/uniform，返回 `training_ids,model,grid,suggestions,summary`。可行域依据母液浓度；重复点排除。

### 服务模块 `experiment_service.py`

`ExperimentService(controller)` 在 Server成功绑定后创建；自有SQLite、锁、停止事件、后台tick线程；`close()`只停止调度并保存，不主动驱动硬件。

- `snapshot()` 返回 `{stocks,calibrations,projects,samples,bottles,photo_jobs,rounds,camera,running,message}`。集合字段统一列表；bottles每项 `{bottle,sample_id}`，sample至少含id/project_id/bottle/status/target/recipe/mixed_at/frames/label/exclusion。
- `handle(op, body)` 支持：`stock`, `calibration`, `project`, `preview`, `enqueue`, `start`, `pause`, `stop`, `release`, `review`, `exclude`, `camera`, `capture`, `retry_photo`, `suggest`, `approve`。
- 所有返回dict并含ok；preview返回 `preview`；project返回 `project`；suggest返回 `round`。
- project输入 `name,salt_port,temperature_c,total_mass_g,safe_mass_limit_g,photo_offsets_s,min_settle_s,max_observe_s`。冻结stocks/calibrations/method快照。enqueue输入 `project_id,recipes:[{peg_fraction,salt_fraction,total_mass_g?,replicates?}],bottles?:[1..5]`；队列不会覆盖占用瓶。start只集中检查，不自动预充。
- camera输入 `ready,rois,width,height`，保存映射版本/心跳；snapshot.photo_jobs带待拍jobs，单job单样品可先实现；capture输入 `job_id,png_base64,analysis,captured_at,mapping_version`，保存原图，验证样品/计时/ROI版本，重复提交幂等。失败可retry_photo（不动液路）。image路径通过 `image_path(image_id)`；导出 `export_zip(project_id=None)` 返回bytes，含JSON、CSV、原图与可读报告。
- review输入 `sample_id,classification(single/two_phase/uncertain),note,frame_id,clear:true/false`。eligible必须人确认clear、符合最早静置时间、对应当前generation、样品完整、未排除；未充分等待可记录但不用于训练。
- suggest输入 `project_id,method,count,ranges...`；approve输入 `round_id,indices:[...]` 将审核候选加入队列；禁止静默重复。
- `before_manual(payload)` 在Controller.start内（校验连接和busy之后）调用；自动任务通过内部 `action:'experiment_recipe',experiment_sample_id,plan` 区分，普通动作暂停队列并记录样品扰动。
- `on_action_event(event,payload,plan=None,error=None)`：Controller worker发started/completed/failed；服务记录动作快照，最终混合结束用plan.mixing_finished_at。线程启动前先持久化样品preparing，避免完成回调丢失。不要持有服务锁调用controller.start/stop/snapshot（避免锁倒序）。

### HTTP（根代理集成）

GET `/api/lab/state`, `/api/lab/image/{image_id}`, `/api/lab/export?project_id=...`；POST `/api/lab/{op}`。沿用本地令牌和Origin检查。自动动作仅由服务编译，不接受外部直接提交experiment_recipe。

### 界面 `lab.js` / `lab.css` 与原界面整合

六页：设备与校准、实验设计、运行监控、图像判相与复核、相图与主动学习、数据与报告。原手动控件ID保留。页面不能用innerHTML插入用户文本。常驻连接/泵读回/阀位/当前样品/任务/停止；硬件急停文字注明实体按钮。

原console.js导出 `window.ATPSCamera`：`status()`返回ready/rois/width/height，`capture()`从实时video抓新帧返回png_base64、analysis、captured_at、rois/width/height；不能用导入或旧冻结帧充当自动拍照。lab.js轮询服务及发送camera心跳，due job拍照并提交。相机和ROI变化更新映射，自动实验启动前集中核对。

## 验证与提交

先增加质量换算、标定、队列、停止/重启、图像关联、训练排除和两轮选点的有效测试，再实现。保留已有回归，浏览器用假设备服务跑完整流程，绝不发送真实仪器命令。更新中文操作指南、README、桌面构建说明。末尾审核、完整测试后一次提交并推送；实机流体精度/消泡/分相准确率明确仍待验证。
