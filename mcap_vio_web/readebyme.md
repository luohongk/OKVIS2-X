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





  终端 1，启动后端：

  EGO_WEB_MAX_CONCURRENCY=1 \
  /root/OKVIS2-X/mcap_vio_web/backend/.venv/bin/uvicorn \
  ego_web.main:app \
  --app-dir /root/OKVIS2-X/mcap_vio_web/backend \
  --host 0.0.0.0 \
  --port 8000

  终端 2，启动网页：

  PATH="/root/OKVIS2-X/mcap_vio_web/.node/bin:$PATH" \
  npm --prefix /root/OKVIS2-X/mcap_vio_web/frontend \
  run dev -- --host 0.0.0.0 --port 5173

  浏览器访问：

  http://127.0.0.1:5173

  远程电脑访问 http://服务器IP:5173。我已实际验证后端和前端启动命令都能正常启动。旧的
  mcap_vio_web/teach.md:1 已经过时，其中 8080 和仅使用 Swagger 的说明不要再参考。

  任务控制方面：

  - 可以在“任务中心”直接删除任务。
  - 排队或运行中的任务，点击“删除”后会先取消进程，再永久删除日志、EuRoC 中间数据、结果和数据库记录。
  - 不能“暂停后恢复”。
  - 也没有“只中断、但保留任务记录和结果”的独立按钮。
  - 页面上的 PAUSED 只是后端是否接受任务的状态，不是可点击的暂停控制。

  删除功能当前仍是工作区里的未提交改动。前端删除测试 14/14、后端清理测试 22/22 已通过；不过后端 API 的
  TestClient 回归测试目前存在启动卡住现象，因此正式处理重要任务前，建议先创建一个小测试任务验证一次删除流
  程。