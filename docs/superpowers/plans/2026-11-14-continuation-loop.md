# 同瓶补加与人工复核后自动选点实施计划

> **For agentic workers:** Use superpowers:subagent-driven-development with explicit file ownership; root integrates and commits only after verification.

**Goal:** 按用户给定质量守恒公式，建立瓶号＋可达目标＋补加动作候选，在质量/轮次/墙钟时间预算内自动配样，人工复核后续加，其他瓶继续。

**Architecture:** 保留六页及泵阀协议。新增纯函数补加求解器与服务扩展；每次续加建立新样品观测ID和父记录，瓶内累计质量仅在完整动作成功后按标定回写。中断标记未知并禁止定量续加。密度审计由独立模块提供，未知保持未知，质量交付标定不依赖密度。

**Tech Stack:** Python stdlib/SQLite + existing vanilla JS six-page UI, Windows pywebview package.

**Spec:** 本轮用户完整同瓶补加说明及已确认“默认人工复核后续加；其他瓶可继续运行”。

## Constraints and contracts

- 自动瓶1—5，6仅手动；每项目PEG＋一种盐＋水。单位g及w/w，不把1g当1mL，不改变标定转速追送参数。
- `continuation_domain.preview_addition(request, inventory, stocks, calibrations, geometry_signature)` 返回原preview兼容结构：`kind='continuation'`, `target`最终目标，`initial_inventory`冻结起点，`theoretical_additions_g`/`stock_masses`为来源补加质量，`estimated`为最终累计组成/质量，`plan`只含补加动作，`executable/issues/warnings/composition`，`added_mass_g`与`estimated_duration_s`。
- inventory字段：`known`, `revision`, `total_mass_g`, `peg_mass_g`, `salt_mass_g`, `water_mass_g`, `salt_port`, `sample_id`, `calibration_valid`；空瓶为已确认零质量。`inventory_key(inventory)` 冻结起点，换瓶/中断/修改后旧计划不可执行。
- `_compiled_estimate`与composition_record识别初始物料，模型/相图/导出始终按最终累计组成。实际组分实测默认空。缺少先前适用标定证据不能升级为有效训练点。
- 有界搜索理论最小最终质量及邻近整数步，不保证全局整数最优；在误差限和标定剂量范围内优先小补加、低预计耗时。无解解释原因，不能用完整空瓶配方替代。
- 新服务API：`continuation_preview{project_id,bottle,peg_fraction,salt_fraction}`；`continuation_run{project_id,bottles,peg_min,peg_max,salt_min,salt_max,step,round_budget,added_mass_budget_g,time_budget_s,objective}` 创建设置；`continuation_start/pause/resume{run_id}`。创建/启动分开，均支持request_id；停止复用全局stop。objective为boundary或two_phase_neighborhood（后者仅探索双相邻域，不输出系线组成）。
- snapshot增加`continuation_runs`及各bottle的inventory；run记录预算、已用量、状态、停止/等待原因、下一候选、冻结轮次及sample_ids。自动候选先核对瓶内物料，再按采用组成计算分数。
- 时间预算是从启动算起的墙钟时间（含等待复核）；不在预计动作和有效观察时间已不足时开启新剂。达到预算停止后续，不中断已执行有限剂。失败剂保留预算保守占用和不确定记录。
- 只有有效人工single/two_phase标签允许该瓶下一次续加。不确定/没有照片等待复核或补拍；其他瓶继续。旧记录照片/标定/标签不改写；新加料使旧未拍任务取消并记录原因。
- 中断/重启/手动加减液使当前物料未知，停止自动续加；不重放，换干净空瓶并确认释放才归零。历史中没有充分动作/组成依据的占用瓶不推断为空瓶。
- 各来源密度未知时仍可按g/步标定配样；参考密度附温度/浓度/来源与“非批次实测”标记，不直接覆盖运行标定。固体密度不可当溶液密度。

## Tasks

- [x] Domain: 新增补加质量求解/量化/可达性，更新统一组成计算；测试用户1g→12%/6%、稀释8%/4%、零浓度、负补量、标定下限、超容量、误差、无操作点与未知库存。
- [x] Service: 库存版本、历史链、预算和人工复核循环，迁移备份，失败/停止/并发规划无续跑，导出完整链与自动轮次；假设备跑至少两轮同瓶和另一瓶并行等待。
- [x] UI: 在现有实验设计/监控/复核/相图/导出中提供连续运行设置、库存/剩余容量、三步预览、预算进度和等待原因，稳定幂等请求，不增加第七页。
- [x] Density: 核查默认源配置（未找到现场活动数据库），保存原始供应商/研究参考和无法核实项，界面逐来源审计；未改现场标定。用户确认10口二水试剂30%，新增无水等效浓度换算，12口无水15%。
- [x] Integration: 已接入全后端、判相、浏览器及实际EXE回归，旧数据兼容与文档；本地验收和同提交发布流水线记录最终结果，验证后同步GitHub及新版发布。

## Review focus

- 同一瓶旧图仍对应旧组成；下一轮完成不改变父样品训练坐标。
- 数据库故障、停止与规划/启动竞争仍能优先发停止，计划不会偷偷恢复。
- 人工复核未知/超窗口/失效标定不能用来驱动下一次定量续加。
- 整数步取整改变最终质量时以量化后累计质量核对上限和预算。
- 停止/丢失响应/重启不能重复扣预算、重复创建或重复加料。

## Review resolutions

- 空瓶只有明确确认后才可作为零库存；独立配样从未知起点完成，也不升级为可训练/可续加记录。
- 未复核点用于覆盖与去重，不能用于监督训练；实测比例不能认证绝对质量交付。
- 手动加减液对尚无样品ID的空瓶同样使库存未知；只读状态不扰动。
- 同一动作在不同初始库存具有不同可执行身份；目标文本不改变相同动作身份。
- 预算停调度时间与在途有限动作最终完成时间分开，墙钟包含尾部动作。
- 旧图和父样品禁止补拍到新液体；未知库存、失效标定或停止竞争不自动重放。
- 最终独立审查补入旧运行/批次待发命令隔离、控制器内层调度检查、发送前墙钟重检、启动存档失败显式恢复及忙碌时请求重试；精确复现均已关闭。
