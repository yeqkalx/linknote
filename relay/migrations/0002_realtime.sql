-- LinkNote relay schema v2：实时架构（Durable Objects + WebSocket Hibernation）
--
-- 笔记正文不再写入 D1：正文只经由 WebSocket 转发 / 手机本地缓存，Relay 不落盘。
-- markdown 列保留仅为兼容旧 schema（新代码不写、不读），历史数据在此清空。

UPDATE tasks SET markdown = NULL WHERE markdown IS NOT NULL;
