<template>
  <main>
    <template v-if="!session">
      <h1>光伏组串IV扫描台</h1>
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；交单前组串得先绑上载流够的电缆截面。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </template>

    <template v-else>
      <header class="topbar">
        <h1>光伏组串IV扫描台</h1>
        <nav class="tabs">
          <button :class="{ active: tab === 'scans' }" @click="tab = 'scans'">扫描台账</button>
          <button :class="{ active: tab === 'cables' }" @click="tab = 'cables'">电缆截面</button>
        </nav>
        <div class="who">
          {{ session.username }}（{{ isWriter ? "扫描员·可写" : "观察员·只读" }}）
          <button class="secondary" @click="refreshAll">刷新</button>
          <button class="secondary" @click="logout">退出</button>
        </div>
      </header>

      <!-- ============ 扫描台账页 ============ -->
      <div v-show="tab === 'scans'">
        <section v-if="isWriter">
          <h2>提交扫描单</h2>
          <p class="hint">先到「电缆截面」页给这串绑好截面：没绑、或截面载流低于短路电流，整笔退回，扫描单不留行。</p>
          <div class="grid">
            <div><label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" /></div>
            <div><label>开路电压 V</label><input type="number" step="0.1" v-model="voc" /></div>
            <div><label>短路电流 A</label><input type="number" step="0.1" v-model="isc" /></div>
            <div><label>填充因子</label><input type="number" step="0.01" v-model="ff" /></div>
            <div>
              <label>电缆截面（可留空）</label>
              <input v-model="cableCode" list="cable-options" placeholder="留空则按组串已绑截面" />
              <datalist id="cable-options">
                <option v-for="c in cables" :key="c.section_code" :value="c.section_code"
                  :label="c.bound_string ? '绑 ' + c.bound_string : '未绑串'"></option>
              </datalist>
            </div>
          </div>
          <button :disabled="loading" @click="submit">提交扫描并入队</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>

        <section>
          <h2>扫描单（{{ logs.length }}）</h2>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>截面</th><th>冻结载流</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td>{{ row.cable_section_code || "—" }}</td>
                <td>{{ row.ampacity_a != null ? row.ampacity_a + "A" : "—" }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
              <tr v-if="logs.length === 0"><td colspan="9" class="empty">还没有扫描单</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 电缆截面专页 ============ -->
      <div v-show="tab === 'cables'">
        <!-- 上格：载流 -->
        <section>
          <h2>上格 · 截面载流台账</h2>
          <p class="hint">登记截面编号和允许载流（A）。载流随时可改，但只影响以后交的单；旧单上的载流随单冻结，改册动不了旧单。</p>
          <div v-if="isWriter" class="grid narrow">
            <div><label>截面编号</label><input v-model="newSection" placeholder="例如 YJV-4mm²" /></div>
            <div><label>载流 A</label><input type="number" step="0.1" v-model="newAmpacity" /></div>
            <div class="align-end"><button :disabled="loading" @click="createCable">登记截面</button></div>
          </div>
          <p v-if="cableError" class="err">{{ cableError }}</p>
          <table v-if="cables.length">
            <thead>
              <tr><th>截面编号</th><th>载流 A</th><th>登记人</th><th>最近改册</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in cables" :key="c.section_code">
                <td>{{ c.section_code }}</td>
                <td>
                  <template v-if="isWriter">
                    <input class="inline" type="number" step="0.1"
                           v-model="ampDrafts[c.section_code]"
                           @keyup.enter="saveAmpacity(c)" />
                    <button class="mini" @click="saveAmpacity(c)">改册</button>
                  </template>
                  <template v-else>{{ c.ampacity_a }}A</template>
                </td>
                <td>{{ c.created_by }}</td>
                <td>{{ fmt(c.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-block">还没有电缆</p>
        </section>

        <!-- 中格：绑串 -->
        <section>
          <h2>中格 · 绑串</h2>
          <p class="hint">一份截面只能绑一串，一串也只能被一份截面绑。两人同时抢同一编号，只有一份成功。解绑后才能换串。</p>
          <table v-if="cables.length">
            <thead>
              <tr><th>截面编号</th><th>载流 A</th><th>绑的组串</th><th>绑定时间</th><th v-if="isWriter">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in cables" :key="'bind-' + c.section_code">
                <td>{{ c.section_code }}</td>
                <td>{{ c.ampacity_a }}A</td>
                <td>
                  <span v-if="c.bound_string" class="tag ok">{{ c.bound_string }}</span>
                  <span v-else class="tag pending">未绑</span>
                </td>
                <td>{{ c.bound_at ? fmt(c.bound_at) : "—" }}</td>
                <td v-if="isWriter">
                  <template v-if="!c.bound_string">
                    <input class="inline" v-model="bindDrafts[c.section_code]"
                           placeholder="组串编号" @keyup.enter="bind(c)" />
                    <button class="mini" :disabled="loading" @click="bind(c)">绑这串</button>
                  </template>
                  <button v-else class="mini danger" :disabled="loading" @click="unbind(c)">解绑</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-block">还没有电缆</p>
        </section>

        <!-- 下格：挡回样例 -->
        <section>
          <h2>下格 · 挡回样例（{{ rejections.length }}）</h2>
          <p class="hint">没绑截面、或载流低于短路电流的单子整笔退回，记在这里；扫描台账上不留行。</p>
          <table>
            <thead>
              <tr><th>时间</th><th>组串</th><th>截面</th><th>截面载流</th><th>Isc</th><th>挡回原因（人话）</th><th>经手</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in rejections" :key="r.id">
                <td>{{ fmt(r.rejected_at) }}</td>
                <td>{{ r.string_code }}</td>
                <td>{{ r.cable_section_code || "—" }}</td>
                <td>{{ r.cable_ampacity_a != null ? r.cable_ampacity_a + "A" : "—" }}</td>
                <td>{{ r.isc_a }}A</td>
                <td class="reason">{{ r.reason }}</td>
                <td>{{ r.rejected_by }}</td>
              </tr>
              <tr v-if="rejections.length === 0"><td colspan="7" class="empty">还没有挡回记录</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </template>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const tab = ref("scans");
const logs = ref([]);
const cables = ref([]);
const rejections = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const cableCode = ref("");
const newSection = ref("");
const newAmpacity = ref("");
const ampDrafts = reactive({});
const bindDrafts = reactive({});
const error = ref("");
const cableError = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString("zh-CN", { hour12: false });
}
async function getJson(path) {
  const res = await fetch(path, { headers: headers() });
  if (res.status === 401) { logout(); throw new Error("未登录"); }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "请求失败");
  }
  return res.json();
}
async function refreshLogs() {
  try { logs.value = await getJson("/api/logs"); } catch { /* 401 已处理 */ }
}
async function refreshCables() {
  try {
    cables.value = await getJson("/api/cables");
    for (const c of cables.value) {
      if (ampDrafts[c.section_code] === undefined) ampDrafts[c.section_code] = c.ampacity_a;
      if (bindDrafts[c.section_code] === undefined) bindDrafts[c.section_code] = c.bound_string || "";
    }
  } catch { /* 401 已处理 */ }
}
async function refreshRejections() {
  try { rejections.value = await getJson("/api/rejections"); } catch { /* 401 已处理 */ }
}
async function refreshAll() {
  await Promise.all([refreshLogs(), refreshCables(), refreshRejections()]);
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  cables.value = [];
  rejections.value = [];
  localStorage.removeItem("pv_session");
}
async function callApi(path, method = "POST", body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...headers() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { logout(); throw new Error("未登录"); }
  if (!res.ok) throw new Error(data.detail || "操作失败");
  return data;
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    await callApi("/api/logs", "POST", {
      string_code: stringCode.value,
      voc_v: Number(voc.value),
      isc_a: Number(isc.value),
      fill_factor: Number(ff.value),
      cable_section_code: cableCode.value || "",
    });
    stringCode.value = voc.value = isc.value = ff.value = cableCode.value = "";
    await refreshAll();
  } catch (e) {
    error.value = e.message;
    await refreshRejections();
  } finally { loading.value = false; }
}
async function createCable() {
  cableError.value = "";
  loading.value = true;
  try {
    await callApi("/api/cables", "POST", {
      section_code: newSection.value,
      ampacity_a: Number(newAmpacity.value),
    });
    newSection.value = newAmpacity.value = "";
    await refreshCables();
  } catch (e) { cableError.value = e.message; }
  finally { loading.value = false; }
}
async function saveAmpacity(c) {
  cableError.value = "";
  try {
    await callApi(`/api/cables/${encodeURIComponent(c.section_code)}`, "PATCH", {
      ampacity_a: Number(ampDrafts[c.section_code]),
    });
    await refreshCables();
  } catch (e) { cableError.value = e.message; }
}
async function bind(c) {
  cableError.value = "";
  loading.value = true;
  try {
    await callApi(`/api/cables/${encodeURIComponent(c.section_code)}/binding`, "POST", {
      string_code: (bindDrafts[c.section_code] || "").trim(),
    });
    await Promise.all([refreshCables(), refreshLogs()]);
  } catch (e) { cableError.value = e.message; }
  finally { loading.value = false; }
}
async function unbind(c) {
  cableError.value = "";
  loading.value = true;
  try {
    await callApi(`/api/cables/${encodeURIComponent(c.section_code)}/binding`, "DELETE");
    bindDrafts[c.section_code] = "";
    await refreshCables();
  } catch (e) { cableError.value = e.message; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0; font-size: 1.35rem; }
h2 { color: #bbf7d0; font-size: 1rem; margin: 0 0 0.6rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.hint { color: #a7f3d0; font-size: 0.82rem; margin: 0 0 0.8rem; }
.topbar { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
.tabs { display: flex; gap: 0.4rem; }
.tabs button { background: #14532d; border: 1px solid #166534; }
.tabs button.active { background: #16a34a; }
.who { margin-left: auto; color: #a7f3d0; font-size: 0.85rem; display: flex; align-items: center; gap: 0.4rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.2rem; }
input.inline { width: 11rem; display: inline-block; margin: 0 0.4rem 0 0; padding: 0.3rem 0.5rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0.6rem 1rem; margin-bottom: 0.8rem; }
.grid.narrow { grid-template-columns: 2fr 1fr auto; align-items: end; }
.align-end { display: flex; justify-content: flex-start; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.mini { padding: 0.3rem 0.7rem; font-size: 0.82rem; margin: 0; }
button.danger { background: #b91c1c; }
.err { color: #fecaca; }
.reason { color: #fde68a; font-size: 0.85rem; }
.empty { text-align: center; color: #86efac; padding: 1rem; }
.empty-block { color: #86efac; background: #022c22; border: 1px dashed #166534; border-radius: 6px; padding: 1.2rem; text-align: center; margin: 0; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: middle; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
