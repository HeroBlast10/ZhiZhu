"""
stealth.py — 反检测 JavaScript 注入模块

参考 hg3386628/zhihu-scraper 和 yuchenzhu-research/zhihu-scraper 的反爬策略。
集成 WebGL、Canvas、AudioContext 指纹伪装以及 navigator.webdriver 覆盖。
"""

STEALTH_JS = """
// 覆盖 navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true
});

// 添加 Chrome 浏览器特有属性
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// 覆盖 Permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
    Promise.resolve({state: Notification.permission}) :
    originalQuery(parameters)
);

// 伪造 WebGL 渲染器信息
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.apply(this, [parameter]);
};

// 随机化 Canvas 指纹
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    if (type === 'image/png' && this.width === 16 && this.height === 16) {
        const canvas = document.createElement('canvas');
        canvas.width = this.width;
        canvas.height = this.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(this, 0, 0);
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const data = imageData.data;
        for (let i = 0; i < data.length; i += 4) {
            data[i] = data[i] + Math.floor(Math.random() * 10) - 5;
            data[i+1] = data[i+1] + Math.floor(Math.random() * 10) - 5;
            data[i+2] = data[i+2] + Math.floor(Math.random() * 10) - 5;
        }
        ctx.putImageData(imageData, 0, 0);
        return origToDataURL.apply(canvas, arguments);
    }
    return origToDataURL.apply(this, arguments);
};

// 修改 AudioContext 指纹
const audioContext = window.AudioContext || window.webkitAudioContext;
if (audioContext) {
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function() {
        const channelData = origGetChannelData.apply(this, arguments);
        if (channelData.length > 20) {
            const noise = 0.0001;
            for (let i = 0; i < Math.min(channelData.length, 500); i++) {
                channelData[i] = channelData[i] + (Math.random() * noise * 2 - noise);
            }
        }
        return channelData;
    };
}

// 随机化硬件并发数
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8 + Math.floor(Math.random() * 4),
    configurable: true
});

// 随机化设备内存
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
    configurable: true
});

// 覆盖 plugins 长度（正常浏览器有 plugin）
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
    configurable: true
});

// 覆盖 languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
    configurable: true
});
"""
