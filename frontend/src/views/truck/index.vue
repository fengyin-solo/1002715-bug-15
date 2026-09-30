<template>
  <section class="page" data-module="truck">
    <header class="page-head">
      <div>
        <h2>内集卡调度管理</h2>
        <p class="page-desc">按车队尺度守住派车底线：油量低于下限、维修期内、已报废车辆不能派发；超时连续作业先安排归队。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记内集卡</button>
        <button class="btn" type="button" @click="exportRows">导出派车清单</button>
      </div>
    </header>

    <!-- 派车看板：可用车数随派车单实时重算 -->
    <div class="stat-row">
      <article v-for="item in boardCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="board-grid">
      <article class="board-card">
        <h3>车队尺度</h3>
        <table class="mini-table">
          <thead><tr><th>车队</th><th>油量下限</th><th>连续作业时限</th></tr></thead>
          <tbody>
            <tr v-for="rule in fleetRules" :key="rule.所属车队">
              <td>{{ rule.所属车队 }}</td>
              <td>{{ rule.油量下限 }}%</td>
              <td>{{ rule.连续作业时限 }} 小时</td>
            </tr>
          </tbody>
        </table>
      </article>
      <article class="board-card warn">
        <h3>超时连续作业·优先归队</h3>
        <p v-if="!overtimeReturn.length" class="board-empty">暂无超时车辆</p>
        <ul v-else class="board-list">
          <li v-for="item in overtimeReturn" :key="String(item.id)">
            <span>{{ item.车牌号码 }} · {{ item.司机姓名 }}（{{ item.所属车队 }}）已连续作业 {{ item.连续作业时长 }} 小时</span>
            <button class="link" type="button" @click="runAction('完成归队', item)">先安排归队</button>
          </li>
        </ul>
      </article>
      <article class="board-card">
        <h3>当前有效派车单（{{ boardOrders.length }}）</h3>
        <p v-if="!boardOrders.length" class="board-empty">暂无在途任务</p>
        <ul v-else class="board-list">
          <li v-for="order in boardOrders" :key="String(order.派车单号)">
            <span>{{ order.派车单号 }}｜{{ order.车牌号码 }}｜{{ order.司机姓名 }}｜{{ order.任务编号 }} {{ order.任务名称 }}</span>
          </li>
        </ul>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>集卡编号</span>
        <input v-model="keyword" placeholder="按集卡编号检索" />
      </label>
      <label class="filter-item">
        <span>派车状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row.集卡编号 }}</td>
          <td><a class="link" href="javascript:void(0)" @click="openDetail(row)">{{ row.车牌号码 }}</a></td>
          <td>{{ row.所属车队 }}</td>
          <td>{{ row.当前任务 || '—' }}</td>
          <td>{{ row.当前位置 || '—' }}</td>
          <td>{{ row.司机姓名 || '—' }}</td>
          <td>{{ row.燃油余量 }}%</td>
          <td>{{ row.连续作业时长 }} 小时</td>
          <td>{{ row.维修截止日 || '—' }}</td>
          <td><span class="badge" :class="badgeClass(row.派车状态)">{{ row.派车状态 }}</span></td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDispatch(row)">派发任务</button>
            <button class="link" type="button" @click="runAction('完成归队', row)">完成归队</button>
            <button class="link" type="button" @click="openRepair(row)">登记维修</button>
            <button class="link" type="button" @click="runAction('维修放行', row)">维修放行</button>
            <button class="link danger" type="button" @click="runAction('车辆报废', row)">车辆报废</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无内集卡调度数据，可先登记内集卡</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条内集卡调度记录</span>
      <span v-if="feedback.message" :class="feedback.ok ? 'ok-text' : 'error-text'">{{ feedback.message }}</span>
    </footer>

    <!-- 派发任务弹层：任务编号、任务名称随动作提交 -->
    <div v-if="dispatchForm.open" class="modal-mask" @click.self="closeDispatch">
      <div class="modal">
        <h3>派发任务 · {{ dispatchForm.plate }}</h3>
        <p class="modal-desc">司机：{{ dispatchForm.driver }}｜车队：{{ dispatchForm.fleet }}</p>
        <label class="form-item">
          <span>任务编号 *</span>
          <input v-model="dispatchForm.taskId" placeholder="如 T-20260930-20" />
        </label>
        <label class="form-item">
          <span>任务名称</span>
          <input v-model="dispatchForm.taskName" placeholder="如 后场箱区转运" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeDispatch">取消</button>
          <button class="btn primary" type="button" :disabled="dispatchForm.saving" @click="confirmDispatch">
            {{ dispatchForm.saving ? '提交中…' : '确认派发' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 登记维修弹层：必须填维修截止日 -->
    <div v-if="repairForm.open" class="modal-mask" @click.self="closeRepair">
      <div class="modal">
        <h3>登记维修 · {{ repairForm.plate }}</h3>
        <label class="form-item">
          <span>维修截止日 *</span>
          <input v-model="repairForm.deadline" type="date" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeRepair">取消</button>
          <button class="btn primary" type="button" :disabled="repairForm.saving" @click="confirmRepair">
            {{ repairForm.saving ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 明细弹层：与列表行同一份后端投影，车牌、司机、派车状态对不上即可当场发现 -->
    <div v-if="detail.open" class="modal-mask" @click.self="detail.open = false">
      <div class="modal">
        <h3>内集卡明细 · {{ detail.data?.集卡编号 }}</h3>
        <table class="mini-table detail-table">
          <tbody>
            <tr v-for="field in detailFields" :key="field">
              <th>{{ field }}</th><td>{{ detail.data?.[field] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detail.open = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type BoardCard = { label: string; value: number }
type FleetRule = { 所属车队: string; 油量下限: number; 连续作业时限: number }
type BoardOrder = {
  派车单号: string
  车牌号码: string
  司机姓名: string
  任务编号: string
  任务名称: string
}
type Board = {
  cards: BoardCard[]
  fleetRules: FleetRule[]
  available: Row[]
  overtimeReturn: Row[]
  blocked: Row[]
  orders: BoardOrder[]
}

const ENDPOINT = '/api/truck'
const columns = [
  '集卡编号', '车牌号码', '所属车队', '当前任务', '当前位置',
  '司机姓名', '燃油余量', '连续作业时长', '维修截止日', '派车状态',
]
const detailFields = columns
const statuses = ['待命', '执行中', '维修中', '已报废']

const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const feedback = reactive({ ok: true, message: '' })

const boardCards = ref<BoardCard[]>([])
const fleetRules = ref<FleetRule[]>([])
const overtimeReturn = ref<Row[]>([])
const boardOrders = ref<BoardOrder[]>([])

const dispatchForm = reactive({
  open: false, saving: false, id: 0, plate: '', driver: '', fleet: '',
  taskId: '', taskName: '',
})
const repairForm = reactive({ open: false, saving: false, id: 0, plate: '', deadline: '' })
const detail = reactive<{ open: boolean; data: Row | null }>({ open: false, data: null })

function badgeClass(state: string | number | null): string {
  if (state === '可派发') return 'badge-ok'
  if (state === '执行中') return 'badge-busy'
  return 'badge-stop'
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  feedback.ok = false
  feedback.message = '内集卡登记入口尚未接入审批流'
}

// 服务端报错原样带出：HTTP 层读 detail，动作层读 message，不再用笼统提示盖掉
function extractServerError(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object') {
    const data = payload as { detail?: unknown; message?: unknown }
    if (typeof data.detail === 'string') return data.detail
    if (typeof data.message === 'string') return data.message
  }
  return `接口返回 ${status}，操作未生效`
}

function openDispatch(row: Row) {
  Object.assign(dispatchForm, {
    open: true, saving: false,
    id: Number(row.id), plate: String(row.车牌号码 ?? ''),
    driver: String(row.司机姓名 ?? ''), fleet: String(row.所属车队 ?? ''),
    taskId: '', taskName: '',
  })
}

function closeDispatch() {
  dispatchForm.open = false
}

async function confirmDispatch() {
  if (!dispatchForm.taskId.trim()) {
    feedback.ok = false
    feedback.message = '请填写任务编号'
    return
  }
  dispatchForm.saving = true
  await submitAction(dispatchForm.id, '派发任务', {
    任务编号: dispatchForm.taskId.trim(),
    任务名称: dispatchForm.taskName.trim(),
  })
  dispatchForm.saving = false
  dispatchForm.open = false
}

function openRepair(row: Row) {
  Object.assign(repairForm, {
    open: true, saving: false, id: Number(row.id),
    plate: String(row.车牌号码 ?? ''), deadline: '',
  })
}

function closeRepair() {
  repairForm.open = false
}

async function confirmRepair() {
  if (!repairForm.deadline) {
    feedback.ok = false
    feedback.message = '请选择维修截止日'
    return
  }
  repairForm.saving = true
  await submitAction(repairForm.id, '登记维修', { 维修截止日: repairForm.deadline })
  repairForm.saving = false
  repairForm.open = false
}

async function runAction(action: string, row: Row) {
  await submitAction(Number(row.id), action, {})
}

async function submitAction(id: number, action: string, values: Record<string, string>) {
  feedback.message = ''
  try {
    const response = await request(`${ENDPOINT}/${id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...values } }),
    })
    const payload = (await response.json().catch(() => null)) as
      | { ok?: boolean; message?: string }
      | null
    if (!response.ok || !payload?.ok) {
      feedback.ok = false
      feedback.message = extractServerError(payload, response.status)
    } else {
      feedback.ok = true
      feedback.message = payload.message ?? '操作已生效'
    }
  } catch (error) {
    feedback.ok = false
    feedback.message = error instanceof Error ? error.message : '内集卡调度操作失败'
  }
  await Promise.all([reload(), loadBoard()])
}

async function openDetail(row: Row) {
  detail.open = true
  detail.data = row
  try {
    // 详情单独拉一次，确认两处读到的车牌号码、司机、派车状态一致
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      feedback.ok = false
      const payload = await response.json().catch(() => null)
      feedback.message = extractServerError(payload, response.status)
      return
    }
    detail.data = (await response.json()) as Row
  } catch (error) {
    feedback.ok = false
    feedback.message = error instanceof Error ? error.message : '明细读取失败'
  }
}

async function reload() {
  feedback.message = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      feedback.ok = false
      const payload = await response.json().catch(() => null)
      feedback.message = extractServerError(payload, response.status)
      return
    }
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    feedback.ok = false
    feedback.message = error instanceof Error ? error.message : '内集卡调度列表读取失败'
  }
}

async function loadBoard() {
  try {
    const response = await request(`${ENDPOINT}/board`)
    if (!response.ok) return
    const payload = (await response.json()) as Board
    boardCards.value = payload.cards
    fleetRules.value = payload.fleetRules
    overtimeReturn.value = payload.overtimeReturn
    boardOrders.value = payload.orders
  } catch {
    // 看板取不到时保留上一次的卡片，不打断列表操作
  }
}

onMounted(() => {
  void reload()
  void loadBoard()
})
</script>
