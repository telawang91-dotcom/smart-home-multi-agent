// ============================================================
// 全屋智能 Multi-Agent 系统 - Node.js 中间层
// 职责:
//   1. 前端静态文件服务
//   2. 手机/电脑 WebSocket 实时同步
//   3. 转发分析请求到 Python Agent Engine
//   4. Agent 流转事件实时推送给前端
// ============================================================

const http = require('http');
const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');

// ========== 配置 ==========
const PORT = 8080;
const AGENT_ENGINE_URL = process.env.AGENT_ENGINE_URL || 'http://localhost:8081';

// ========== 存储控制状态 ==========
let controlState = {
    room: 'bedroom',
    temp: {
        bedroom: 24.0,
        living: 24.0,
        bathroom: 24.0
    },
    rh: 60,
    mode: 'manual',
    lastUpdate: Date.now()
};

// 最近一次 Agent 分析结果缓存
let lastAgentResult = null;

// ========== HTTP 转发工具函数 ==========

/**
 * 转发请求到 Python Agent Engine
 */
function forwardToAgentEngine(apiPath, method, body) {
    return new Promise((resolve, reject) => {
        const url = new URL(apiPath, AGENT_ENGINE_URL);
        const options = {
            hostname: url.hostname,
            port: url.port,
            path: url.pathname,
            method: method,
            headers: {
                'Content-Type': 'application/json',
            },
            timeout: 30000,
        };

        const req = http.request(options, (res) => {
            let data = '';
            res.on('data', chunk => { data += chunk; });
            res.on('end', () => {
                try {
                    resolve({ status: res.statusCode, data:JSON.parse(data) });
                } catch (e) {
                    resolve({ status: res.statusCode, data: data });
                }
            });
        });

        req.on('error', (e) => {
            reject(e);
        });

        req.on('timeout', () => {
            req.destroy();
            reject(new Error('Agent Engine 请求超时'));
        });

        if (body) {
            req.write(typeof body === 'string' ? body : JSON.stringify(body));
        }
        req.end();
    });
}

/**
 * SSE 流式转发 - 连接 Agent Engine 的 SSE 接口，逐步推送给前端 WebSocket
 */
function streamFromAgentEngine(sensorData, sceneId, onEvent, onDone, onError) {
    const url = new URL('/api/analyze/stream', AGENT_ENGINE_URL);
    const postData = JSON.stringify({
        sensor_data: sensorData,
        scene_id: sceneId
    });

    const options = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
        },
        timeout: 60000,
    };

    const req = http.request(options, (res) => {
        let buffer = '';

        res.on('data', (chunk) => {
            buffer += chunk.toString();
            // 解析 SSE 格式
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const eventData = JSON.parse(line.substring(6));
                        if (eventData.event_type === 'done') {
                            onDone();
                        } else {
                            onEvent(eventData);
                        }
                    } catch (e) {
                        // 忽略解析错误
                    }
                }
            }
        });

        res.on('end', () => {
            onDone();
        });
    });

    req.on('error', (e) => {
        onError(e);
    });

    req.on('timeout', () => {
        req.destroy();
        onError(new Error('SSE 流式请求超时'));
    });

    req.write(postData);
    req.end();
}

// ========== 创建 HTTP 服务器 ==========
const server = http.createServer(async (req, res) => {
    // 处理CORS
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
        res.writeHead(200);
        res.end();
        return;
    }

    // ===== 原有 API: 状态管理 =====
    if (req.url === '/api/state' && req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(controlState));
        return;
    }

    if (req.url === '/api/state' && req.method === 'POST') {
        let body = '';
        req.on('data', chunk => { body += chunk.toString(); });
        req.on('end', () => {
            try {
                const newState = JSON.parse(body);
                controlState = { ...controlState, ...newState, lastUpdate: Date.now() };
                wss.clients.forEach(client => {
                    if (client.readyState === WebSocket.OPEN) {
                        client.send(JSON.stringify({ type: 'state', data: controlState }));
                    }
                });
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ success: true }));
            } catch (e) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: e.message }));
            }
        });
        return;
    }

    // ===== 新增 API: Agent 分析（转发到 Python） =====
    if (req.url === '/api/agent/analyze' && req.method === 'POST') {
        let body = '';
        req.on('data', chunk => { body += chunk.toString(); });
        req.on('end', async () => {
            try {
                const result = await forwardToAgentEngine('/api/analyze', 'POST', body);
                lastAgentResult = result.data;
                res.writeHead(result.status, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify(result.data));
            } catch (e) {
                console.error('Agent Engine 转发失败:', e.message);
                res.writeHead(502, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    error: 'Agent Engine 不可用',
                    message: e.message,
                    hint: '请确保 Python Agent Engine 已启动 (python agent_engine/main.py)'
                }));
            }
        });
        return;
    }

    // ===== 新增 API: Agent Engine 健康检查 =====
    if (req.url === '/api/agent/health' && req.method === 'GET') {
        try {
            const result = await forwardToAgentEngine('/api/health', 'GET', null);
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                node_server: 'ok',
                agent_engine: result.data,
            }));
        } catch (e) {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                node_server: 'ok',
                agent_engine: { status: 'offline', error: e.message },
            }));
        }
        return;
    }

    // ===== 新增 API: 预设异常场景列表 =====
    if (req.url === '/api/agent/scenarios' && req.method === 'GET') {
        try {
            const result = await forwardToAgentEngine('/api/scenarios', 'GET', null);
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result.data));
        } catch (e) {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: e.message }));
        }
        return;
    }

    // ===== 新增 API: 运行预设异常场景 =====
    if (req.url.startsWith('/api/agent/scenarios/') && req.method === 'POST') {
        const scenarioId = req.url.split('/api/agent/scenarios/')[1].split('?')[0];
        try {
            const result = await forwardToAgentEngine('/api/scenarios/' + scenarioId, 'POST', '{}');
            lastAgentResult = result.data;
            res.writeHead(result.status, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result.data));
        } catch (e) {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: e.message }));
        }
        return;
    }

    // ===== 新增 API: 决策历史 =====
    if (req.url.startsWith('/api/agent/decisions') && req.method === 'GET') {
        try {
            const qs = req.url.includes('?') ? req.url.split('?')[1] : '';
            const result = await forwardToAgentEngine('/api/data/decisions?' + qs, 'GET', null);
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result.data));
        } catch (e) {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: e.message }));
        }
        return;
    }

    // ===== 新增 API: 调度器状态 =====
    if (req.url === '/api/agent/scheduler' && req.method === 'GET') {
        try {
            const result = await forwardToAgentEngine('/api/scheduler/status', 'GET', null);
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result.data));
        } catch (e) {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: e.message }));
        }
        return;
    }

    // ===== 新增 API: 获取最近一次分析结果 =====
    if (req.url === '/api/agent/last-result' && req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(lastAgentResult || { status: 'no_result' }));
        return;
    }

    // ===== 静态文件服务 =====
    const serverDir = __dirname;
    let urlPath = req.url.split('?')[0];

    if (urlPath === '/' || urlPath === '') {
        urlPath = '/gai.html';
    }

    let filePath = path.join(serverDir, urlPath.startsWith('/') ? urlPath.substring(1) : urlPath);

    const resolvedPath = path.resolve(filePath);
    const resolvedDir = path.resolve(serverDir);
    if (!resolvedPath.startsWith(resolvedDir)) {
        res.writeHead(403);
        res.end('Forbidden');
        return;
    }

    const extname = String(path.extname(filePath)).toLowerCase();
    const mimeTypes = {
        '.html': 'text/html; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.json': 'application/json',
        '.png': 'image/png',
        '.jpg': 'image/jpg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.wav': 'audio/wav',
        '.mp4': 'video/mp4',
        '.woff': 'application/font-woff',
        '.ttf': 'application/font-ttf',
        '.eot': 'application/vnd.ms-fontobject',
        '.otf': 'application/font-otf',
        '.wasm': 'application/wasm'
    };

    const contentType = mimeTypes[extname] || 'application/octet-stream';

    fs.readFile(filePath, (error, content) => {
        if (error) {
            if (error.code == 'ENOENT') {
                console.error('文件未找到:', filePath);
                res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
                res.end(`<h1>404 - 文件未找到</h1><p>请求的文件: ${req.url}</p><p>实际路径: ${filePath}</p>`);
            } else {
                console.error('服务器错误:', error);
                res.writeHead(500);
                res.end('Server error: ' + error.code);
            }
        } else {
            res.writeHead(200, { 'Content-Type': contentType });
            res.end(content, 'utf-8');
        }
    });
});

// ========== 创建 WebSocket 服务器 ==========
const wss = new WebSocket.Server({ server });

wss.on('connection', (ws, req) => {
    const clientIP = req.socket.remoteAddress || 'unknown';
    console.log(`新客户端连接: ${clientIP}`);

    // 发送当前状态给新连接的客户端
    try {
        ws.send(JSON.stringify({ type: 'state', data: controlState }));
    } catch (e) {
        console.error('发送初始状态失败:', e);
    }

    // 接收客户端消息
    ws.on('message', (message) => {
        try {
            const data = JSON.parse(message);
            console.log('收到消息:', data.type, '来自:', clientIP);

            if (data.type === 'update') {
                // 更新状态（来自手机控制面板）
                controlState = { ...controlState, ...data.data, lastUpdate: Date.now() };
                console.log('状态已更新');

                let broadcastCount = 0;
                wss.clients.forEach(client => {
                    if (client !== ws && client.readyState === WebSocket.OPEN) {
                        try {
                            client.send(JSON.stringify({ type: 'state', data: controlState }));
                            broadcastCount++;
                        } catch (e) {
                            console.error('广播消息失败:', e);
                        }
                    }
                });
                console.log(`已广播给 ${broadcastCount} 个客户端`);

                try {
                    ws.send(JSON.stringify({ type: 'ack', success: true }));
                } catch (e) {
                    console.error('发送确认失败:', e);
                }

            } else if (data.type === 'deviceType') {
                console.log(`设备类型: ${data.deviceType}`);
                wss.clients.forEach(client => {
                    if (client !== ws && client.readyState === WebSocket.OPEN) {
                        try {
                            client.send(JSON.stringify({ type: 'deviceConnected', deviceType: data.deviceType }));
                        } catch (e) {
                            console.error('广播设备连接失败:', e);
                        }
                    }
                });

            } else if (data.type === 'sensorUpdate') {
                if (data.data) {
                    controlState = { ...controlState, ...data.data, lastUpdate: Date.now() };
                    console.log('传感器数据已更新');

                    let broadcastCount = 0;
                    wss.clients.forEach(client => {
                        if (client !== ws && client.readyState === WebSocket.OPEN) {
                            try {
                                client.send(JSON.stringify({ type: 'state', data: controlState }));
                                broadcastCount++;
                            } catch (e) {
                                console.error('广播传感器数据失败:', e);
                            }
                        }
                    });
                    console.log(`已广播传感器数据给 ${broadcastCount} 个客户端`);
                }

            } else if (data.type === 'getState') {
                try {
                    ws.send(JSON.stringify({ type: 'state', data: controlState }));
                } catch (e) {
                    console.error('发送状态失败:', e);
                }

            } else if (data.type === 'agentAnalyze') {
                // ===== 新增: Agent 分析请求（流式） =====
                console.log('收到 Agent 分析请求, 场景:', data.scene_id || '自定义');

                // 通知发送者：分析开始
                try {
                    ws.send(JSON.stringify({
                        type: 'agentEvent',
                        event_type: 'pipeline_start',
                        timestamp: Date.now() / 1000
                    }));
                } catch (e) { /* ignore */ }

                // 构建传感器数据
                const sensorData = data.sensor_data || {
                    room: controlState.room || 'bedroom',
                    temperature: controlState.temp || { bedroom: 25, living: 25, bathroom: 25 },
                    humidity: controlState.rh || 55,
                    hour: new Date().getHours(),
                    mmwave_radar: controlState.mmwave_radar || 'active',
                    pir: controlState.pir !== undefined ? controlState.pir : true,
                    door_contact: controlState.door_contact || false,
                    fall_risk: controlState.fall_risk || false,
                    activity: controlState.activity || 'sitting',
                    prediction_enabled: controlState.prediction_enabled !== undefined ? controlState.prediction_enabled : true,
                };

                // 流式调用 Agent Engine
                streamFromAgentEngine(
                    sensorData,
                    data.scene_id || null,
                    // onEvent: 每个 Agent 步骤的事件
                    (event) => {
                        // 推送给请求发起者
                        if (ws.readyState === WebSocket.OPEN) {
                            try {
                                ws.send(JSON.stringify({
                                    type: 'agentEvent',
                                    ...event
                                }));
                            } catch (e) { /* ignore */ }
                        }
                        // 广播给所有其他客户端
                        wss.clients.forEach(client => {
                            if (client !== ws && client.readyState === WebSocket.OPEN) {
                                try {
                                    client.send(JSON.stringify({
                                        type: 'agentEvent',
                                        ...event
                                    }));
                                } catch (e) { /* ignore */ }
                            }
                        });
                    },
                    // onDone: 分析完成
                    () => {
                        console.log('Agent 分析完成');
                        const doneMsg = JSON.stringify({
                            type: 'agentEvent',
                            event_type: 'pipeline_complete',
                            timestamp: Date.now() / 1000
                        });
                        // 广播完成事件
                        wss.clients.forEach(client => {
                            if (client.readyState === WebSocket.OPEN) {
                                try { client.send(doneMsg); } catch (e) { /* ignore */ }
                            }
                        });
                    },
                    // onError: 错误处理
                    (error) => {
                        console.error('Agent Engine 流式请求失败:', error.message);
                        if (ws.readyState === WebSocket.OPEN) {
                            try {
                                ws.send(JSON.stringify({
                                    type: 'agentEvent',
                                    event_type: 'error',
                                    error: error.message,
                                    hint: '请确保 Python Agent Engine 已启动',
                                    timestamp: Date.now() / 1000
                                }));
                            } catch (e) { /* ignore */ }
                        }
                    }
                );

            } else if (data.type === 'agentAnalyzeSync') {
                // ===== 新增: Agent 同步分析请求 =====
                console.log('收到 Agent 同步分析请求');

                const sensorData = data.sensor_data || {
                    room: controlState.room || 'bedroom',
                    temperature: controlState.temp || { bedroom: 25, living: 25, bathroom: 25 },
                    humidity: controlState.rh || 55,
                    hour: new Date().getHours(),
                    mmwave_radar: controlState.mmwave_radar || 'active',
                    pir: controlState.pir !== undefined ? controlState.pir : true,
                    door_contact: controlState.door_contact || false,
                    fall_risk: controlState.fall_risk || false,
                    activity: controlState.activity || 'sitting',
                    prediction_enabled: controlState.prediction_enabled !== undefined ? controlState.prediction_enabled : true,
                };

                forwardToAgentEngine('/api/analyze', 'POST', {
                    sensor_data: sensorData,
                    scene_id: data.scene_id || null
                }).then(result => {
                    lastAgentResult = result.data;
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({
                            type: 'agentResult',
                            data: result.data
                        }));
                    }
                    // 广播给其他客户端
                    wss.clients.forEach(client => {
                        if (client !== ws && client.readyState === WebSocket.OPEN) {
                            try {
                                client.send(JSON.stringify({
                                    type: 'agentResult',
                                    data: result.data
                                }));
                            } catch (e) { /* ignore */ }
                        }
                    });
                }).catch(error => {
                    console.error('Agent Engine 同步请求失败:', error.message);
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({
                            type: 'agentError',
                            error: error.message,
                            hint: '请确保 Python Agent Engine 已启动'
                        }));
                    }
                });
            }

        } catch (e) {
            console.error('处理消息错误:', e);
        }
    });

    ws.on('close', (code, reason) => {
        console.log(`客户端断开连接: ${clientIP}, 代码: ${code}`);
    });

    ws.on('error', (error) => {
        console.error(`WebSocket错误 (${clientIP}):`, error.message);
    });

    // 心跳检测
    ws.isAlive = true;
    ws.on('pong', () => {
        ws.isAlive = true;
    });
});

// 心跳检测：每30秒检查一次连接
const interval = setInterval(() => {
    wss.clients.forEach((ws) => {
        if (ws.isAlive === false) {
            console.log('检测到死连接，关闭');
            return ws.terminate();
        }
        ws.isAlive = false;
        try {
            ws.ping();
        } catch (e) {
            // 忽略错误
        }
    });
}, 30000);

// ========== 启动服务器 ==========
server.listen(PORT, '0.0.0.0', () => {
    console.log('============================================================');
    console.log('  全屋智能 Multi-Agent 系统 - Node.js 中间层');
    console.log('============================================================');
    console.log(`  HTTP:      http://0.0.0.0:${PORT}`);
    console.log(`  WebSocket: ws://0.0.0.0:${PORT}`);
    console.log(`  Agent API: http://localhost:${PORT}/api/agent/analyze`);
    console.log(`  健康检查:  http://localhost:${PORT}/api/agent/health`);
    console.log('------------------------------------------------------------');
    console.log(`  Agent Engine: ${AGENT_ENGINE_URL}`);
    console.log('------------------------------------------------------------');
    console.log('  请确保 Python Agent Engine 已启动:');
    console.log('    cd agent_engine && python main.py');
    console.log('');
    console.log('  手机端: http://你的IP:' + PORT + '/mobile-control.html?server=你的IP');
    console.log('  电脑端: http://你的IP:' + PORT + '/gai.html?server=你的IP');
    console.log('============================================================');
});