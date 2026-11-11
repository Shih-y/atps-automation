# COM6 单总线实机版 API（本地127.0.0.1）

GET /api/token 得到token，POST必须带X-Control-Token及application/json。GET /api/state为完整快照；/api/config是完整配置。POST响应{ok,state}；HTTP错误为{ok:false,error}。异步动作接收后仍须轮询state：FAULT表示动作失败，不能仅看POST 200。

- /api/connect {}：默认实机、串口取config.communication.port（初始COM6）。可用{port:"COM6"}覆盖，也兼容{mode:"real",pump_port:"COM6",valve_port:"COM6"}；泵阀必须同口。mode:"demo"会拒绝，不提供模拟入口。连接只打开一次共享串口，不自动开泵、切阀、扫描地址或进行启动检查；程序启动本身不连接设备。/api/disconnect {}；/api/stop {}。
- /api/config {config:完整配置}，仅断开时可保存。geometry/liquids/pump/valve/communication任何改动使已有定量和复位验证失效。快照geometry_signature用于记录校准所对应配置；不能复用旧水载液档案。
- /api/action {action:new_samples}：人工确认所有样品已恢复验证初态。
- /api/action {action:select_port,port:11}：停泵并核验阀位，只选口，不吸液、不追送。不推断管内液态；没有待追送液段时会清除公共管就绪标记，需要重新复位。7—10禁用。
- /api/action {action:wash}：只用15口正向清洗至废液，结束后common=wet、reset_sequence=washed，不会自动吹扫。
- /api/action {action:purge}：只用16口正向空气吹扫，需前后人工观察。仅当紧邻的15口清洗已完成且符合已验证复位条件时，进入air_reset_verified；单独吹扫为air_unverified，不能证明洗净或绝对干燥。
- /api/action {action:reset}：15水正向→16空气正向→人工确认残水状态。
- /api/action {action:prime,source:11,pulse:{steps:100,rpm:1,timeout_s:10},max_pulses:20}：每个有限脉冲后询问液面。到达A标线后标记该来源预充，公共管仍需清洗复位。
- /api/action {action:aspirate,profile_id}：仅吸取，完成后停泵并创建pending_plug。需来源支管已预充、公共管就绪、目标瓶初态与档案匹配；不增加成功交付次数。
- /api/action {action:chase,pending_id}：只追送当前锁定液段至它的目标瓶。建议总是携带当前pending_id；如提供profile_id/sample/target也须匹配。完成后该瓶dose_count加1、condition=continued、公共管wet。
- /api/action {action:discard_loaded}：明确弃液，人工确认废液与通气条件后15水正向带出液段到废液，再16口吹扫；不记成功交付。过期液段也可弃液。若液段曾与目标口连通，弃液后该目标condition=unknown并标记uncertain_delivery，不能直接继续首次/连续档案；须评估或更换样品及支管。
- /api/action {action:dose,profile_id,bubble:false}；组合“吸取＋追送”，可选随后鼓泡，**不含清洗**，结束后公共管仍湿。
- /api/action {action:run,items:[{profile_id,bubble:false}]}；组合队列，开始时若已air_reset_verified则省去重复起始清洗；否则先复位。每次交付及可选鼓泡之后都执行15清洗＋16吹扫，下一剂复用同一组分步动作。每瓶最多5次0.20mL目标交付。
- /api/action {action:bubble,profile_id}；只能对刚交付的同一样品、同一档案继续鼓泡。
- /api/action {action:commission,port:16,direction:aspirate|dispense,pulse}；最多500步、5rpm、30秒、每连接20次，状态随后未知。反转只允许1—6；正转不允许从样品抽吸。限制为保守软件调试上限，不证明其实际体积/压力安全。
- /api/action {action:confirm_reset}；仅可复核本连接已有完整15清洗→16吹扫记录，不能用一次人工点击替代实际步骤或绕过首次调试。首次校准使用commission有限调试，不凭此建立定量状态。
- /api/action {action:clear,sample:1}；确认气泡消失、液面稳定，开始该瓶独立静置计时。
- /api/action {action:magnetic_stop,sample:1,settle_s:300}；手动磁搅对照停转确认，软件不控制搅拌器。
- /api/action {action:prepare_capture,targets:[1,2,3,4,5,6]}；别名action:capture。每瓶均消泡确认且达到等待时间后创建单相机单排正面全景批次，元数据view_direction=fixed_front_row，并保存当前geometry_signature与几何快照。
- /api/ack {id,ok:true}，取消ok:false；prime_front提示用{ id,choice:reached|more|abort }。提示10分钟超时停止，不自动确认。
- /api/capture {batch_id,png_base64,width,height,captured_at,alignment_confirmed:true,rois:[{sample,x,y,width,height}]}；PNG必须RGB/RGBA8位，保存原始帧、ROI及每瓶时间，不自动判相。
- /api/label {batch_id,sample,classification:single|two_phase|uncertain,note}：人工判相，与原图关联。
- /api/discard {batch_id}，显式放弃未完成图像批次。GET /api/images/{batch_id}/{filename} 查看登记原图。
- /api/calibration/report：接收下述校准数据，返回{ok:true,report,rows,saved_file}。保存原始请求、标准行、报告、完整配置、geometry_signature到data/calibration_reports。仅计算与归档，不自动写入档案或启用实机。

状态快照包括selected_port、reset_sequence和pending_plug。后者保存id/source/target/profile_id、完整profile快照、geometry_signature、UTC created_at/expires_at、单调时钟创建/过期值及动态remaining_s/expired。操作日志记录目标提前选口时间target_selected_at。max_plug_hold_s不因选口或页面刷新重新计时；校准与evidence.note须覆盖来源/目标口两种等待阶段及最不利选口等待时间。进程重启或停止只把物理状态标未知，不自动弃液、追送或记成功剂量。

本版几何记录：A/B/C=10/10/71 cm，总长91 cm；11—15支管各80 cm；1—6样品支管各50 cm。不再使用preflight_verified作为连接条件，geometry.measured仅记录用户已给定长度，不用于启动拦截。连接不要求泵状态映射；实际泵动作仍需要有效状态码解释，避免把未知设备回包当作完成。此项属于动作处理，不是启动检查表。

共享COM6只打开一个串口，并串行收发两种帧。软件地址初始泵1、阀2，是待与设备实际地址匹配的通信配置，不表示已读取或修改设备。共享模式不允许阀0（泵广播）、同址、泵地址落入阀组播范围，且两设备通信速率/帧格式须一致。不自动发送地址设置、地址扫描、广播或组播命令。

有待追送液段时，其他液路操作、new_samples、commission、队列、消泡/成像任务均被禁止，只能选择该液段的锁定目标口、追送或明确弃液复位，也可停止。切到锁定目标本身不记交付，仍需及时追送；期限已经到达时只能弃液或停止。追送在人工观察后以及阀位确认后、泵动作前都重检期限，超时不自动移动液段。没有泵动作的过期拒绝保留液段供明确处理；通信/运动故障仍停止并使状态未知。

校准报告的新手表单请求示例：

```json
{
  "measurement_mode":"stock_equivalent", "profile_key":"stock11-vial1-first-v1",
  "source":11, "sample":1, "condition":"first", "target_ml":0.2,
  "assay_method":"填写独立母液当量测定方法、温度、液位、等待窗口、动作参数与原始记录路径",
  "delivery_values":[0.201,0.200,0.199,0.201,0.200,0.200,0.199,0.201,0.200,0.200],
  "water_carry_values_ml":[0.001,0.001,0.001],
  "cross_carry_values_ml":[0.0005,0.0005,0.0005]
}
```

上述数值只是格式示例，不能作为实测证据。stock_equivalent的delivery_values单位为独立测得的母液当量mL。清水重力法用measurement_mode=water_gravimetry、source=15、density_g_ml及以g为单位的delivery_values；仅纯水可用质量/水密度换算，不接受将混合排液质量除以母液密度当母液量。高级请求可直接提交{rows:[标准行对象],target_ml:0.2}，标准列见calibration_report.py。报告hardware_authorized始终false；默认缺失的残水/污染数据不会当零，也不会放行。

档案通过网页校准表单建立，字段约束见controller.validate_profile。产品profiles默认为空，不内置模拟档案；每档案明确source/sample/first或continued，独立aspirate/chase/bubble有限脉冲，以及target_ml=0.2、bubble_count/gap、settle_s、max_hold_s、immersion_mm。validated=true还必须有匹配geometry_signature及证据n≥10、实际母液回收均值0.19—0.21mL、RSD≤5%、残水≤0.005mL、交叉污染≤1%和可追溯note。这是建议筛选门槛，不是本设备已达到的指标。鼓泡另需bubble_validated=true。图像判相始终人工复核。

全局动作硬上限20000步、30rpm、120秒覆盖阀切换与泵运行；有限脉冲、硬上限、单次超时与人工观察同时生效。这些上限不能替代实物小脉冲预实验。串口故障、超时、取消、软件停止均使状态未知并要求重连，绝不自动重发运动指令。

补充：鼓泡采用独立短脉冲上限，每次≤500步、≤5rpm、动作超时≤5秒（包含阀/串口通信），最多3次，间隔0.1—5秒。有效气量、背压及混合充分性须实测，上限本身不证明安全或足够混合。profile的immersion_mm是加料前条件，首次空瓶可填0；bubble_immersion_mm必须>0且鼓泡前人工检查实际浸没。max_hold_s限定来源预充/连续加料停留范围；max_plug_hold_s单独限定A段液塞吸取后等待（1—600秒）。initial_dose_count_min/max必须明确本档案覆盖的初始交付次数/液位范围，first必须0/0，continued必须从1开始；验证某一初始液位不能推广到所有连续加料。

校准与控制不是循环依赖：实机确认装配/方向/状态码后，先用commission的有限脉冲调试。按纸面SOP依次选择15正转清洗、16正转排水、来源口正转预充和吸取、目标样品口反转追送；每次停止观察，并记录所有动作的步数、转速、超时、实际前沿及收集结果。相同条件至少完成规定重复，使用网页校准表单或calibration_report.py计算报告。commission不声称体积、不标记已校准，也不要求已有定量档案。收集充分证据后，停机断开，在首次设置与校准档案表单填入实测动作和证据，经人工核对启用；高级JSON为备选。不得将示例100步当成0.20mL。复位证据须覆盖15支管初始充空气的首次清洗，填写reset.covers_initial_air_filled_water_branch=true；仅在15管已充水时得到的洗涤数据不能用于首次空管。

以下为待填档案结构示例，所有动作数值为占位，validated必须保持false直至完成实测。将其加入config.profiles；第一次只覆盖1号空瓶，从11口交付。

```json
{
  "id": "stock11-vial1-first-measured-v1",
  "source": 11, "sample": 1, "condition": "first", "target_ml": 0.2,
  "aspirate": {"steps": 100, "rpm": 1, "timeout_s": 10},
  "chase": {"steps": 100, "rpm": 1, "timeout_s": 10},
  "bubble": {"steps": 100, "rpm": 1, "timeout_s": 5},
  "bubble_count": 1, "bubble_gap_s": 1, "settle_s": 300,
  "max_hold_s": 600, "max_plug_hold_s": 20,
  "initial_dose_count_min": 0, "initial_dose_count_max": 0,
  "immersion_mm": 0, "bubble_immersion_mm": 2,
  "validated": false, "bubble_validated": false,
  "geometry_signature": "从当前状态复制，不能沿用旧配置",
  "evidence": {"n": 0, "mean_ml": 0, "rsd_percent": 0,
    "residual_water_ml": 0, "carryover_percent": 0,
    "water_blank_n": 0, "carryover_n": 0,
    "note": "待记录原始数据路径、独立母液当量测量方法、初始液位/停留/温度/液体批次与审核人；零不是检测结果"}
}
```

实机筛选还检查water_blank_n≥3及carryover_n≥3。单次残水≤0.005mL只是暂拟筛选线；5次加料仍可能累计0.025mL，即1mL配方的2.5%。需另做整瓶配方组成与相界附近敏感性验证，不可由单次档案通过宣称整瓶误差≤1%。calibration_report.py输出的hardware_authorized始终为false。

任何几何、母液身份、通信或wash/purge动作变化都会使旧档案验证失效；同ID的aspirate/chase/鼓泡等定量参数变化也撤销验证。每次保存配置、连接设备都把完整配置快照写入data/config_history，动作写入data/events.jsonl。停止后旧图像批次只作历史查看。操作前的纯输入错误会显示“操作未执行”而保留已知液路状态；运动已尝试后的异常仍停止并使状态未知。

实测协议补充（2026-11-09）：pump.status_ack=[254]仅用于停止与有限运动的接收应答，不能用于查询完成；status_busy=[4]、status_ok=[0]用于查询。完成必须状态0且剩余步数0。动态设速必须应答与读回一致。当前实例无水测试已确认这组值，不代表其他固件。
