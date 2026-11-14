# ade26f2 完整修改意见落实（2026-11-14）

用户本轮任务单为验收依据。保留六页、通信模块和桌面外壳；修复后统一提交发布，测试禁止真实串口/摄像头动作。

1. 停止不依赖数据库和日志；停止结果明确requested/confirmed/unconfirmed。自动启动必须project_id且固定批次范围。
2. domain提供 `composition_record(sample, project=None)`：返回basis(estimated/measured/unavailable)、peg_fraction、salt_fraction、deviation、valid、reasons、units。实测必须独立组分分析及方法，整瓶称量不构成组分实测。`execution_key(plan)`忽略目标瓶但包含来源/整数步/转速/追送/混合；相同动作标同一点，重复实验显式标记。
3. 标定增加status(trial/validated/invalid)、min_steps/max_steps、sample_bottles、bottle_scope_evidence。旧标定迁移为trial并记录旧比例外推事实，不捏造验证。project允许trial用于调试配样，但训练须经适用范围核对；UI明确状态。组成误差限默认0.01绝对质量分数（1个百分点），可配置。
4. service负责schema_version与升级前SQLite备份、项目范围调度、来源相关兼容、结构化排除与更正、执行/瓶位/判相/资格分离、观察期限按照片时间、导出引用闭包、幂等队列与批准。
5. 新service操作保持现有接口并扩展：start{project_id}; cancel{sample_id}; remake{sample_id,request_id}; extend_observation{sample_id,max_observe_s,offsets_s,reason}; photo_failure{job_id,reason}; correct_exclusion{sample_id,exclusion_id,reason}; measured_composition{sample_id,peg_fraction,salt_fraction,method,evidence}; backup{project_id?}; project_version{project_id,...覆盖条件}。UI与service代理直接协调字段。
6. UI重构为统一上下文与六页模块，不再多层包装api/render/draw。相图/导出/模型共用后端composition字段；当前项目启动预览，任务逐个拍照失败处理，样品照片时序与ROI裁剪，明确实测温度是手工记录而非温控。补拍用下拉选择；项目/全部导出与备份。
7. 根代理负责Controller停止、手动指令校验在扰动之前（改为已验证运动边界回调）、确认测试竞态、Windows EXE构建和发布流水线；service不编辑controller/server，UI不编辑service，domain不编辑其他模块。
8. 回归覆盖用户全部场景，真实后端假设备/合成视频浏览器测试；EXE显式隔离数据目录启动，不连设备，检查六页/备份导出/关闭。更新操作说明和发布说明；最终提交号和SHA256可追溯。

进度：以上实现完成并经过独立审查。370项Python回归、27项合成判相测试、六页15项/原手动35项/控制器联通11项浏览器检查及两瓶合成视频闭环通过。实际EXE已验证首次启动、六页、合成相机、导出、正常关窗、旧数据库升级备份及重启不续跑。发布前按最终源码提交重建并复测EXE；发布包保留EXE与同名应用级.NET配置文件。软件通过不代表真实交付和判相精度已验证。
