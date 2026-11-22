"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

``scenario_label`` 另有规则版本 / 聚类轮次 / 重放等领域子路径，优先注册，
再挂通用资源路由，避免被 ``/api/{module}/{entry_id}`` 抢先匹配。
"""
from __future__ import annotations

from app.routers import resource, scenario_label

ROUTERS = [scenario_label, resource]
