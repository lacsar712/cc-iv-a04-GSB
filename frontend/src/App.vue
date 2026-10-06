<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；交单前先绑电缆截面，载流扛不住短路电流的组串不许入队。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <button :class="{ active: page === 'scan' }" @click="page = 'scan'">扫描台</button>
        <button :class="{ active: page === 'cable' }" @click="page = 'cable'">电缆截面</button>
        <span class="who">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "观察员·只读" }}）</span>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <!-- ============ 扫描台 ============ -->
      <div v-show="page === 'scan'">
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p class="hint">交单前先到「电缆截面」页给这串绑好截面；没绑或载流低于短路电流，整笔退回，挡回样例可在该页下格查看。</p>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <div class="rowhead">
            <h2>扫描单队列</h2>
            <button class="secondary" @click="refresh">刷新</button>
          </div>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>绑定截面</th><th>随单载流</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td>{{ row.cable_spec || "—" }}</td>
                <td>{{ row.ampacity_a != null ? row.ampacity_a + " A" : "—" }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 电缆截面专页 ============ -->
      <div v-show="page === 'cable'">
        <!-- 上格：载流册 -->
        <section>
          <div class="rowhead"><h2>① 载流册（截面允许载流）</h2></div>
          <p v-if="!cables.length" class="empty">还没有电缆{{ isWriter ? "，先在下面登记一根" : "" }}</p>
          <table v-else>
            <thead>
              <tr><th>截面编号</th><th>允许载流</th><th>绑给的组串</th><th>绑串人</th><th v-if="isWriter">改册</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in cables" :key="c.id">
                <td>{{ c.spec_code }}</td>
                <td>
                  <template v-if="isWriter && editId === c.id">
                    <input class="inline" type="number" step="0.1" v-model="editAmp" /> A
                  </template>
                  <template v-else>{{ c.ampacity_a }} A</template>
                </td>
                <td>{{ c.bound_string || "未绑串" }}</td>
                <td>{{ c.bound_by || "—" }}</td>
                <td v-if="isWriter">
                  <button v-if="editId !== c.id" class="secondary small" @click="startEdit(c)">改载流</button>
                  <span v-else>
                    <button class="small" @click="saveAmp(c)">保存</button>
                    <button class="secondary small" @click="editId = null">取消</button>
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="isWriter" class="inlineform">
            <input v-model="newSpec" placeholder="截面编号，例如 YJV-4mm2" />
            <input v-model="newAmp" type="number" step="0.1" placeholder="允许载流 A" />
            <button :disabled="loading" @click="addCable">登记电缆</button>
          </div>
          <p v-if="cableError" class="err">{{ cableError }}</p>
        </section>

        <!-- 中格：绑串 -->
        <section v-if="isWriter">
          <h2>② 绑串（先绑截面，再去交单）</h2>
          <div class="inlineform">
            <select v-model="bindSpec">
              <option value="" disabled>选截面编号</option>
              <option v-for="c in cables" :key="c.id" :value="c.spec_code">{{ c.spec_code }}（{{ c.ampacity_a }} A）</option>
            </select>
            <input v-model="bindString" placeholder="组串编号，例如 阵列A-串01" />
            <button :disabled="loading" @click="bindCable">绑给这串</button>
          </div>
          <p class="hint">一根电缆只能绑一串、一串只能绑一根电缆；两人抢同一截面时只准先提交的一份成功。</p>
          <p v-if="bindError" class="err">{{ bindError }}</p>
        </section>

        <!-- 下格：挡回样例 -->
        <section>
          <div class="rowhead"><h2>③ 挡回样例（载流不够 / 没绑截面的整笔退回）</h2></div>
          <p v-if="!rejections.length" class="empty">还没有挡回记录</p>
          <table v-else>
            <thead>
              <tr><th>时间</th><th>组串</th><th>Isc</th><th>截面</th><th>载流</th><th>挡回原因</th><th>提交人</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in rejections" :key="r.id">
                <td>{{ fmtTime(r.rejected_at) }}</td>
                <td>{{ r.string_code }}</td>
                <td>{{ r.isc_a }} A</td>
                <td>{{ r.cable_spec || "未绑" }}</td>
                <td>{{ r.ampacity_a != null ? r.ampacity_a + " A" : "—" }}</td>
                <td class="reason">{{ r.reason }}</td>
                <td>{{ r.rejected_by }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const page = ref("scan");
const logs = ref([]);
const cables = ref([]);
const rejections = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const cableError = ref("");
const bindError = ref("");
const loading = ref(false);
const newSpec = ref("");
const newAmp = ref("");
const bindSpec = ref("");
const bindString = ref("");
const editId = ref(null);
const editAmp = ref("");
let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers(json) {
  const h = { Authorization: "Bearer " + session.value.token };
  if (json) h["Content-Type"] = "application/json";
  return h;
}
async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (res.status === 401) { logout(); throw new Error("未登录"); }
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}
function fmtTime(s) {
  return s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "";
}
async function refresh() {
  if (!session.value) return;
  const [l, c, r] = await Promise.all([
    api("/api/logs", { headers: headers() }),
    api("/api/cables", { headers: headers() }),
    api("/api/rejections", { headers: headers() }),
  ]);
  if (l.ok) logs.value = l.data;
  if (c.ok) cables.value = c.data;
  if (r.ok) rejections.value = r.data;
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
    await refresh();
    timer = setInterval(refresh, 2000);
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
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const { ok, data } = await api("/api/logs", {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    if (!ok) { error.value = data.detail || "提交失败，单子被退回"; await refresh(); return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch (e) { if (session.value) error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function addCable() {
  cableError.value = "";
  const { ok, data } = await api("/api/cables", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ spec_code: newSpec.value, ampacity_a: Number(newAmp.value) }),
  });
  if (!ok) { cableError.value = data.detail || "登记失败"; return; }
  newSpec.value = newAmp.value = "";
  await refresh();
}
function startEdit(c) {
  editId.value = c.id;
  editAmp.value = String(c.ampacity_a);
}
async function saveAmp(c) {
  cableError.value = "";
  const { ok, data } = await api(`/api/cables/${c.id}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify({ ampacity_a: Number(editAmp.value) }),
  });
  if (!ok) { cableError.value = data.detail || "改册失败"; return; }
  editId.value = null;
  await refresh();
}
async function bindCable() {
  bindError.value = "";
  const { ok, data } = await api("/api/bindings", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ spec_code: bindSpec.value, string_code: bindString.value.trim() }),
  });
  if (!ok) { bindError.value = data.detail || "绑串失败"; await refresh(); return; }
  bindString.value = "";
  bindSpec.value = "";
  await refresh();
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1040px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.75rem; }
h2 { font-size: 1rem; margin: 0 0 0.75rem; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
input.inline { width: 5.5rem; display: inline-block; margin: 0 0.3rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.small { padding: 0.25rem 0.6rem; font-size: 0.8rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar button { margin-right: 0; }
.topbar button.active { background: #15803d; outline: 2px solid #86efac; }
.who { color: #a7f3d0; font-size: 0.9rem; margin-left: auto; margin-right: 0.5rem; }
.rowhead { display: flex; align-items: center; justify-content: space-between; }
.rowhead button { margin: 0 0 0.5rem; }
.inlineform { display: flex; gap: 0.5rem; align-items: flex-start; margin-top: 0.75rem; flex-wrap: wrap; }
.inlineform input, .inlineform select { width: auto; min-width: 11rem; margin-bottom: 0; flex: 1; }
.inlineform button { flex: 0 0 auto; }
.hint { color: #a7f3d0; font-size: 0.82rem; margin: 0.5rem 0 0; }
.empty { color: #fde68a; background: #854d0e33; border: 1px dashed #ca8a04; border-radius: 6px; padding: 0.75rem 1rem; text-align: center; }
.err { color: #fecaca; background: #7f1d1d66; border-radius: 6px; padding: 0.5rem 0.75rem; }
.reason { color: #fecaca; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
