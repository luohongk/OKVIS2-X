目前可视化网站使用 5173 端口，后端 API 使用 8000 端口。

  终端 1：启动后端

  EGO_WEB_MAX_CONCURRENCY=1 \
  /root/OKVIS2-X/mcap_vio_web/backend/.venv/bin/uvicorn \
  ego_web.main:app \
  --app-dir /root/OKVIS2-X/mcap_vio_web/backend \
  --host 0.0.0.0 \
  --port 8000

  终端 2：启动前端

  PATH="/root/OKVIS2-X/mcap_vio_web/.node/bin:$PATH" \
  npm --prefix /root/OKVIS2-X/mcap_vio_web/frontend \
  run dev -- --host 0.0.0.0 --port 5173

  浏览器访问

  http://127.0.0.1:5173

  局域网或云平台访问：

  http://<服务器IP>:5173

  需要在云平台开放或代理 5173 端口。

  你之前启动的 8080 是后端 API 端口，不是当前的可视化前端；前端开发代理现在连接后端 8000。后续 Task 13
  会将前端和后端合并到同一个端口。

  另外，Task 12 已验证完成：

  前端测试：32 passed
  TypeScript：通过
  生产构建：通过