/* Web ATS 统一 HTTP 请求层：处理 JSON、超时与稳定错误信息。 */
(function exposeApi(global) {
  async function request(path, options = {}, hooks = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    hooks.onPending?.();

    try {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      const result = contentType.includes("application/json")
        ? await response.json()
        : { ok: response.ok, message: `HTTP ${response.status}` };
      hooks.onResult?.(result, response);
      return result;
    } catch (error) {
      const message = error.name === "AbortError"
        ? "命令超时，请检查后端状态"
        : "后端未连接";
      const result = { ok: false, message };
      hooks.onError?.(result, error);
      return result;
    } finally {
      clearTimeout(timeout);
    }
  }

  global.AtsApi = Object.freeze({ request });
}(window));
