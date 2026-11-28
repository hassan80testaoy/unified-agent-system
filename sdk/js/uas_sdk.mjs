/**
 * Zero-dependency ESM JavaScript SDK for Unified Agent System (Node 18+ built-in fetch).
 */
export class UnifiedAgentSDK {
  /**
   * @param {{ baseUrl?: string, token?: string | null }} [opts]
   */
  constructor({ baseUrl = "http://127.0.0.1:8010", token = null } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {unknown} [body]
   */
  async _request(method, path, body = undefined) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    const url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
    const res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let parsed = {};
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      parsed = { raw: text };
    }
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}: ${JSON.stringify(parsed)}`);
      err.status = res.status;
      err.body = parsed;
      throw err;
    }
    return parsed;
  }

  async login(email, password) {
    const res = await this._request("POST", "/api/auth/login", { email, password });
    this.token = res.token || res.access_token || "";
    return this.token;
  }

  health() {
    return this._request("GET", "/api/health");
  }

  runtimeStatus() {
    return this._request("GET", "/api/system/runtime-status");
  }

  listThreads() {
    return this._request("GET", "/api/threads");
  }

  createThread(opts = {}) {
    return this._request("POST", "/api/threads", opts);
  }

  getThread(threadId) {
    return this._request("GET", `/api/threads/${threadId}`);
  }

  sendMessage(threadId, content, opts = {}) {
    return this._request("POST", `/api/threads/${threadId}/messages`, { content, ...opts });
  }

  approvePlan(threadId, designIndex = 0) {
    return this._request("POST", `/api/threads/${threadId}/approve-plan`, { design_index: designIndex });
  }

  updateTodo(threadId, items) {
    return this._request("PUT", `/api/threads/${threadId}/todo`, { items });
  }

  undo(threadId, toSeq) {
    return this._request("POST", `/api/threads/${threadId}/undo`, { to_seq: toSeq });
  }

  fork(threadId, fromSeq = null, newTitle = null) {
    return this._request("POST", `/api/threads/${threadId}/fork`, {
      from_seq: fromSeq,
      new_title: newTitle,
      use_worktree: true,
    });
  }

  listArtifacts(threadId) {
    return this._request("GET", `/api/threads/${threadId}/artifacts`);
  }

  getChanges(threadId) {
    return this._request("GET", `/api/threads/${threadId}/changes`);
  }

  createWorktree(threadId, branchName = null) {
    return this._request("POST", `/api/threads/${threadId}/worktree`, { branch_name: branchName });
  }

  mergeWorktree(threadId) {
    return this._request("POST", `/api/threads/${threadId}/worktree/merge`, {});
  }

  runRace(threadId, prompt, testCommand, candidates) {
    return this._request("POST", `/api/threads/${threadId}/race`, {
      prompt,
      test_command: testCommand,
      candidates,
    });
  }

  spawnSubagent(threadId, role, prompt, useWorktree = false) {
    return this._request("POST", `/api/threads/${threadId}/subagents`, {
      role,
      prompt,
      use_worktree: useWorktree,
    });
  }

  listGoals() {
    return this._request("GET", "/api/goals");
  }

  createGoal(title, outcome, threadId = null) {
    return this._request("POST", "/api/goals", { title, outcome, thread_id: threadId, autopilot: true });
  }

  stepGoal(goalId) {
    return this._request("POST", `/api/goals/${goalId}/step`, {});
  }

  listHooks() {
    return this._request("GET", "/api/hooks");
  }

  createHook(hook) {
    return this._request("POST", "/api/hooks", hook);
  }

  deleteHook(hookId) {
    return this._request("DELETE", `/api/hooks/${hookId}`);
  }
}
