"""
stealth.py — 保守的 Playwright 自动化特征兼容脚本

指纹字段必须在同一页面生命周期内保持自洽。这里不再伪造 Permissions、
WebGL、Canvas、AudioContext、hardwareConcurrency 或 plugins，避免破坏原生
浏览器 API 并制造比 Playwright 默认值更明显的检测特征。
"""

STEALTH_JS = """
(() => {
    const navigatorPrototype = Object.getPrototypeOf(navigator);
    const webdriverDescriptor = Object.getOwnPropertyDescriptor(
        navigatorPrototype,
        'webdriver'
    );

    if (!webdriverDescriptor || webdriverDescriptor.configurable) {
        Object.defineProperty(navigatorPrototype, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
    }
})();
"""
