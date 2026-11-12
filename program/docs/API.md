# 本地泵阀控制 API（2026-11-13）

当前页面使用 `free_debug=true`。取消的是前序观察、校准档案及步骤顺序的按钮门槛；仍需有效参数、真实连接和单一串口任务。不会将重复点击排队。旧清水组合试验 `tubes/water_recipe`、自由调试 `run` 已停用。`free_debug=false` 是历史兼容模式，不是当前页面操作方式。

## 请求与状态

服务仅绑定 `127.0.0.1`。GET `/api/token` 获取令牌；所有 POST 使用 JSON 和 `X-Control-Token`。错误为 `{ok:false,error}`。GET `/api/state` 提供状态；动作返回成功仅代表接收，须轮询 `busy/state/message` 及对应运行记录确认完成。

- `/api/serial-ports`（GET）：枚举串口及 CH340 属性，不扫描设备地址。
- `/api/connect`：`{auto_detect:true}` 重新解析唯一 CH340，或 `{port:"COM6"}` 指定共用串口；泵1、阀2。不会转泵或切阀。没有模拟模式。
- `/api/stop`：取消当前任务、发送停止并确认；成功后保留原连接，旧任务不续跑。无法确认停止则显示异常，不虚报已停。
- `/api/disconnect`：明确关闭设备连接。
- `/api/config`（GET/POST）：读取配置；写入 `{config:完整配置}` 仅能在断开时进行。

`hardware_status` 区分指令、读回和未知状态。方向取自已接受运动指令，不由阀位推断。`stop_requested` 不表示已确认停止。

## 单瓶组合加料

POST `/api/action`：

```json
{
  "action":"recipe", "sample":1,
  "items":[{"source":15,"volume_ml":0.5},{"source":14,"volume_ml":1.0},{"source":13,"volume_ml":0.3}],
  "mix_after":true, "purge_after":true,
  "mixing":{"steps":20,"rpm":5,"timeout_s":10}
}
```

先验证整份请求，再按 `items` 顺序吸取/追送所有液段。各段与各来源之间没有15口清洗、弃液复位或16口吹扫。全部完成后向目标瓶混合一次，再16口向C端吹扫一次（15圈/60 rpm/30秒超时）。默认两个末尾步骤都开启；API允许显式关闭，当前主页固定开启。

每段请求量及参考估算量均不超过0.8 mL。超过上限时等分为足够多段，保留总整数步数；例如默认参考下1 mL为150+150步，2 mL为200+200+200步。更新参考可能使临界值多分一段。全瓶本次请求合计≤10 mL，最多60段；这不表示软件知道瓶内已有体积。0.8 mL是规划限值，不能证明实际液段一定留在A段。

默认45步对应用户报告的端点0.15 mL：总吸取步数=按半入规则取整(请求mL×45/0.15)。各段追送步数=1000+max(0,本段吸取步数−45)。吸取5 rpm，追送15 rpm。更新参考后采用新步数、体积和转速。没有使用C端每圈出水量替代样品端参考。

`recipe_run` 记录 `items[].requested_ml/estimated_ml/chunks/parameters`、总段数、完成段数、指令进度、`current.phase`（aspirate/chase/mixing/purge）、混合及吹扫是否完成、取消与错误。索引从0开始。`actual_ml:null`、`volume_verified:false`；动作完成不等于实测体积成立。中止配方保留已完成进度，不能点继续自动续跑。

兼容 `action:"volume"` 加 `source/sample/volume_ml` 使用同一分段规划，默认末尾吹扫、默认不混合；主页使用 `recipe`。

## 独立动作和管路维护

以下均为 POST `/api/action`：

| action / operation | 参数与动作 |
|---|---|
| `select_port` | `{port:15}`，仅停泵、切阀并核验，允许1—6、11—16 |
| `read_status` | 查询泵与阀，不转泵或切阀 |
| `manual_motion` | `{port:15,rotation:"ccw",rpm:20,turns:2}`；`cw/ccw`，整数圈1—100，rpm0.1—100，动作超时≤120秒；完整配置上限同样生效 |
| `wash` / `purge` / `reset` | 分别15清水、16空气、15→16；wash默认16圈/50rpm，purge默认15圈/60rpm |
| `tubes`, `operation:"refill"` | `{sources:[13,14,15]}`；每口6圈/50rpm，最后16吹扫，无观察弹窗 |
| `tubes`, `operation:"drain_sources"` | `{sources:[13,14,15]}`；来源末端先人工提出液面，每口15圈/60rpm到C，再16吹扫 |
| `tubes`, `operation:"drain_samples"` | `{samples:[1]}`；样品管先人工移到废液，16吹扫后逐口CW1000步/15rpm，结束只切16 |
| `tubes`, `operation:"finish"` | 同时提供sources/samples，先来源排液再样品管排液 |

CCW：所选口→泵→C；CW：C→泵→所选口。手动入口允许反向使用所选端口，由操作者确认接管。每次动作有限且不自动重发运动指令。

分步接口仍保留 `aspirate/chase/dose/bubble`，使用 `debug_parameters:{source,sample,aspirate:{steps,rpm,timeout_s},chase:{steps,rpm,timeout_s},bubble:{steps,rpm,timeout_s}}`。`dose`仅吸取+追送；`bubble`仅混合。没有历史前提门槛，不自动声称管路充满或排净。

## 可选端点参考

POST `/api/reference`：

```json
{"mean_per_dose_ml":0.15,"sample":1,"aspirate_steps":45,"aspirate_rpm":5,"chase_steps":1000,"chase_rpm":15}
```

六个字段都必填。只保存用户实测均值及对应参数，不运动；可断开时保存，动作进行中拒绝更新。允许每剂0.01—1 mL、参考吸取1—100步、参考追送1—3000步、0.1—100rpm（0.1精度）并满足配置及超时。写入 `data/water_dose_reference.json` 后重启仍有效。不捏造重复次数、原始称重或RSD。

POST `/api/action`，`{action:"bench",operation:"dose_water",sample:1,count:1或5,aspirate_steps:45,aspirate_rpm:5,chase_steps:1000,chase_rpm:15}` 执行可选清水测试。历史校准试送保留逐剂16吹扫，用于复现端点参考；正常配方没有逐剂吹扫。因此这份参考不证明连续配方的实际回收量。

POST `/api/bench/report` 提交 `method:"batch_water"`、3组 `readings_g`、`doses_per_group:5`、`density_g_ml`、`balance_resolution_g:0.01`、同一组动作参数和备注，计算并存档且更新清水参考。组平均离散程度不能当作单剂重复性。正式报告 `/api/calibration/report` 仍保留，计算不会自动授权或运行设备。

## 图像和人工复核

摄像头由浏览器显式开启，默认关闭；不镜像，画面右到左为1—5。本地导入图片也可使用。`phase_analysis.js` 输出未验证的单相/两相候选或不确定；人工标签独立保存。

1. POST `/api/imaging/begin {targets:[1,2,3,4,5]}` 建立独立存档批次，返回 `{ok,batch_id,imaging}`。不要求串口连接或消泡确认，不覆盖未完成批次。
2. POST `/api/capture` 提交批次、RGB/RGBA PNG裸base64、width/height、rois、`alignment_confirmed:false`、`allow_partial_rois:true`，以及可选 `analysis`。算法的ROI及瓶号必须与本帧ROI一一对应，`validated:false`。空ROI可保存原图并记录所有尚未定位的瓶号，不虚构识别结果。
3. 独立存档支持 `frame_origin:"camera"`、实际取帧时间 `captured_at`（可早于存档批次创建）；导入图用 `frame_origin:"import",captured_at:null,loaded_at:加载时间`。导入时间不是拍摄时间，不计算其静置时长。原有受时间约束的采集接口仍检查采集时间不早于就绪时间。
4. 保存后 POST `/api/label {batch_id,sample,classification:"single"|"two_phase"|"uncertain",note}` 人工复核；不覆盖原始算法结果。只有实际存在ROI的瓶子可标注。
5. POST `/api/discard {batch_id}` 明确放弃未完成批次；GET `/api/images/{batch_id}/{filename}` 获取登记原图。

存档包含原图、真实ROI、算法方法与候选、拍摄/加载/接收时间、缺失定位、每瓶混合停气时间及人工复核。没有可用时间时记录null，不声称已消泡、已稳定、30秒判相或算法达到特定准确率。
