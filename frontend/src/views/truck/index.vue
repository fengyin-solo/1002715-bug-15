<template>
  <section class="page" data-module="truck">
    <header class="page-head">
      <div>
        <h2>内集卡调度管理</h2>
        <p class="page-desc">
          派车底线：报废车不派、维修期内不派（与油量冲突时以维修期为准）、
          燃油低于本车队下限不派、连续作业超 {{ overtimeLimit }} 小时先归队；同车同趟次只算一单。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记内集卡</button>
        <button class="btn" type="button" @click="exportRows">导出内集卡调度清单</button>
        <button class="btn warn" type="button" :disabled="submitting" @click="recallOvertime">
          超时车一键归队{{ board ? `（${overtimeCard}）` : '' }}
        </button>
      </div>
    </header>

    <!-- 派车看板：卡片数据来自 /board，每次派车/归队后随派车单重算 -->
    <div class="stat-row">
      <article v-for="item in boardCards" :key="item.label" class="stat-card" :class="{ alert: item.label === '超时待归队' && item.value > 0 }">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="fleetRows.length" class="fleet-row">
      <article v-for="fleet in fleetRows" :key="fleet.车队" class="fleet-card">
        <strong>{{ fleet.车队 }}</strong>
        <span>油量下限 {{ fleet.油量下限 }}%</span>
        <span>可用 {{ fleet.可用 }}/{{ fleet.总数 }}</span>
        <span>执行 {{ fleet.执行中 }}</span>
        <span>维修 {{ fleet.维修中 }}</span>
        <span>报废 {{ fleet.已报废 }}</span>
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
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ overtime: row.超时连续作业 }">
          <td>
            <button class="link" type="button" @click="openDetail(Number(row.id))">{{ row.集卡编号 }}</button>
          </td>
          <td>{{ row.车牌号码 }}</td>
          <td>{{ row.所属车队 }}</td>
          <td>{{ row.当前任务 || '—' }}</td>
          <td>{{ row.当前位置 || '—' }}</td>
          <td>{{ row.司机姓名 || '—' }}</td>
          <td :class="{ low: isLowFuel(row) }">
            {{ row.燃油余量 }}
            <small v-if="isLowFuel(row)" class="low">（下限{{ row.油量下限 }}%）</small>
          </td>
          <td>
            <span class="status-tag" :class="statusClass(row.派车状态)">{{ row.派车状态 }}</span>
            <span v-if="row.超时连续作业" class="overtime-tag">超时 {{ row.连续作业小时 }}h</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" :disabled="submitting" @click="openDispatch(row)">派发任务</button>
            <button class="link" type="button" :disabled="submitting" @click="runAction('完成归队', row, {})">完成归队</button>
            <button class="link" type="button" :disabled="submitting" @click="openRepair(row)">登记维修</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无内集卡调度数据，可先登记内集卡</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条内集卡调度记录</span>
      <span v-if="errorMessage" class="error-text" data-role="error">{{ errorMessage }}</span>
    </footer>

    <!-- 派发任务弹窗：趟次必填，用于同车同趟次去重 -->
    <div v-if="dispatchForm.open" class="modal-mask" @click.self="closeForms">
      <div class="modal">
        <h3>派发任务 · {{ dispatchForm.plate }}</h3>
        <p class="modal-tip">
          {{ dispatchForm.fleet }} 派车下限 {{ dispatchForm.floor }}%，
          当前油量 {{ dispatchForm.fuel }}；连续作业超 {{ overtimeLimit }} 小时需先归队。
        </p>
        <label class="form-row"><span>趟次编号 *</span><input v-model="dispatchForm.tripNo" placeholder="如 TRIP-0930-08" /></label>
        <label class="form-row"><span>任务说明</span><input v-model="dispatchForm.task" placeholder="如 V-230 卸船转运" /></label>
        <label class="form-row"><span>司机姓名</span><input v-model="dispatchForm.driver" :placeholder="`留空默认：${dispatchForm.driverPlaceholder}`" /></label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeForms">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitDispatch">提交派车</button>
        </div>
      </div>
    </div>

    <!-- 登记维修弹窗 -->
    <div v-if="repairForm.open" class="modal-mask" @click.self="closeForms">
      <div class="modal">
        <h3>登记维修 · {{ repairForm.plate }}</h3>
        <label class="form-row">
          <span>预计维修时长（小时）</span>
          <input v-model="repairForm.hours" type="number" min="1" placeholder="默认 24 小时" />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeForms">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitRepair">确认登记</button>
        </div>
      </div>
    </div>

    <!-- 车辆明细：与列表读同一份主档数据 + 派车单历史 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal wide">
        <h3>内集卡明细 · {{ detail.集卡编号 }}</h3>
        <dl class="detail-grid">
          <template v-for="field in columns" :key="field">
            <dt>{{ field }}</dt>
            <dd :class="{ low: field === '燃油余量' && isLowFuel(detail) }">{{ detail[field] || '—' }}</dd>
          </template>
          <dt>连续作业</dt>
          <dd>{{ detail.连续作业小时 === null ? '—' : `${detail.连续作业小时} 小时` }}</dd>
          <dt>维修截止</dt>
          <dd>{{ detail.维修截止 || '—' }}</dd>
        </dl>
        <h4>派车单（共 {{ detail.派车单.length }} 条）</h4>
        <table class="data-table inner">
          <thead>
            <tr><th>趟次编号</th><th>任务说明</th><th>车牌号码</th><th>司机姓名</th><th>开始时间</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="order in detail.派车单" :key="String(order.id)">
              <td>{{ order.趟次编号 }}</td>
              <td>{{ order.任务说明 }}</td>
              <td>{{ order.车牌号码 }}</td>
              <td>{{ order.司机姓名 }}</td>
              <td>{{ order.开始时间 }}</td>
              <td>{{ order.已归队 ? '已归队' : '执行中' }}{{ order.超时连续作业 ? '（超时）' : '' }}</td>
            </tr>
            <tr v-if="!detail.派车单.length">
              <td colspan="6" class="empty-state">暂无派车记录</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type DispatchOrder = {
  id: number
  趟次编号: string
  任务说明: string
  车牌号码: string
  司机姓名: string
  开始时间: string | null
  已归队: boolean
  超时连续作业: boolean
}
type Detail = Row & { 派车单: DispatchOrder[] }
type BoardCard = { label: string; value: number }
type FleetStat = {
  车队: string
  总数: number
  可用: number
  执行中: number
  维修中: number
  已报废: number
  油量下限: number
}

const ENDPOINT = '/api/truck'
const columns = ['集卡编号', '车牌号码', '所属车队', '当前任务', '当前位置', '司机姓名', '燃油余量', '集卡状态']
const statuses = ['待命', '执行中', '维修中', '已报废']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const submitting = ref(false)
const detail = ref<Detail | null>(null)
const board = ref<{ cards: BoardCard[]; fleets: FleetStat[] } | null>(null)

const dispatchForm = reactive({
  open: false,
  id: 0,
  plate: '',
  fleet: '',
  floor: 0,
  fuel: '',
  tripNo: '',
  task: '',
  driver: '',
  driverPlaceholder: '',
})
const repairForm = reactive({ open: false, id: 0, plate: '', hours: '' })

const boardCards = computed<BoardCard[]>(() => board.value?.cards ?? [
  { label: '可用集卡', value: 0 },
  { label: '执行中', value: 0 },
  { label: '维修中', value: 0 },
  { label: '已报废', value: 0 },
  { label: '超时待归队', value: 0 },
])
const fleetRows = computed<FleetStat[]>(() => board.value?.fleets ?? [])
const overtimeCard = computed(() => boardCards.value.find((item) => item.label === '超时待归队')?.value ?? 0)
const overtimeLimit = computed(() => {
  const raw = board.value as Record<string, number> | null
  return raw?.['连续作业上限小时'] ?? 8
})

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  // 服务端报错原样带出：这里直接引用后端的未接入提示，不做前端假设
  errorMessage.value = '内集卡登记入口尚未接入审批流'
}

function isLowFuel(row: Row): boolean {
  const fuel = parseFloat(String(row.燃油余量 ?? '').replace('%', ''))
  return Number.isFinite(fuel) && fuel < Number(row.油量下限 ?? 0)
}

function statusClass(status: unknown): string {
  return { 待命: 'st-idle', 执行中: 'st-run', 维修中: 'st-repair', 已报废: 'st-scrap' }[String(status)] ?? ''
}

/** 读取服务端报错：动作接口把缘由放在 message，HTTP 异常放在 detail，都原样取出。 */
async function readServerError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as Record<string, unknown>
    if (typeof payload.message === 'string' && payload.message) {
      return payload.message
    }
    const detail = payload.detail
    if (typeof detail === 'string') {
      return detail
    }
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as { msg?: string }
      return first?.msg ?? '请求参数有误'
    }
  } catch {
    // 响应不是 JSON，落到状态码提示
  }
  return `服务端返回 ${response.status}，操作未生效`
}

async function postAction(id: number, values: Record<string, unknown>): Promise<void> {
  errorMessage.value = ''
  const response = await request(`${ENDPOINT}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  const payload = (await response.json()) as { ok: boolean; message: string }
  if (!response.ok || !payload.ok) {
    // 服务端报的错原样带出，不改写成笼统的“操作失败”
    throw new Error(payload.message || (await readServerError(response)))
  }
  errorMessage.value = payload.message
  await Promise.all([reload(), loadBoard()])
}

function openDispatch(row: Row) {
  Object.assign(dispatchForm, {
    open: true,
    id: Number(row.id),
    plate: String(row.车牌号码 ?? ''),
    fleet: String(row.所属车队 ?? ''),
    floor: Number(row.油量下限 ?? 0),
    fuel: String(row.燃油余量 ?? ''),
    tripNo: '',
    task: '',
    driver: '',
    driverPlaceholder: String(row.司机姓名 ?? ''),
  })
  errorMessage.value = ''
}

function openRepair(row: Row) {
  Object.assign(repairForm, { open: true, id: Number(row.id), plate: String(row.车牌号码 ?? ''), hours: '' })
  errorMessage.value = ''
}

function closeForms() {
  dispatchForm.open = false
  repairForm.open = false
}

async function submitDispatch() {
  if (!dispatchForm.tripNo.trim()) {
    errorMessage.value = '请填写趟次编号'
    return
  }
  submitting.value = true
  try {
    await postAction(dispatchForm.id, {
      action: '派发任务',
      趟次编号: dispatchForm.tripNo.trim(),
      任务说明: dispatchForm.task.trim(),
      司机姓名: dispatchForm.driver.trim(),
    })
    closeForms()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '派车失败'
  } finally {
    submitting.value = false
  }
}

async function submitRepair() {
  const values: Record<string, unknown> = { action: '登记维修' }
  if (repairForm.hours.trim()) {
    values.维修时长 = repairForm.hours.trim()
  }
  submitting.value = true
  try {
    await postAction(repairForm.id, values)
    closeForms()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记维修失败'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row, values: Record<string, unknown>) {
  submitting.value = true
  try {
    await postAction(Number(row.id), { action, ...values })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '内集卡调度操作失败'
  } finally {
    submitting.value = false
  }
}

async function recallOvertime() {
  submitting.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/recall-overtime`, { method: 'POST' })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || (await readServerError(response)))
    }
    errorMessage.value = payload.message
    await Promise.all([reload(), loadBoard()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '超时归队操作失败'
  } finally {
    submitting.value = false
  }
}

async function openDetail(id: number) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) {
      throw new Error(await readServerError(response))
    }
    detail.value = (await response.json()) as Detail
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '明细读取失败'
  }
}

async function loadBoard() {
  try {
    const response = await request(`${ENDPOINT}/board`)
    if (response.ok) {
      board.value = (await response.json()) as typeof board.value
    }
  } catch {
    // 看板读不出来不阻塞列表，卡片保持 0
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) {
    query.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    query.set('status', statusFilter.value)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error(await readServerError(response))
    }
    const payload = (await response.json()) as { items: Row[]; total: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadBoard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '内集卡调度列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.btn.warn { border-color: #d9822b; color: #b35b00; }
.btn[disabled] { opacity: 0.55; cursor: not-allowed; }
.stat-card.alert { border-color: #d92d20; background: #fff5f4; }
.stat-card.alert .stat-value { color: #d92d20; }

.fleet-row { display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.fleet-card {
  display: flex; flex-direction: column; gap: 2px;
  background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; min-width: 150px;
  font-size: 12px; color: var(--muted);
}
.fleet-card strong { font-size: 13px; color: #1f2937; }

tr.overtime { background: #fff8eb; }
.overtime-tag { margin-left: 6px; font-size: 11px; color: #b35b00; background: #fef0c7; border-radius: 4px; padding: 1px 6px; }
.status-tag { font-size: 12px; border-radius: 4px; padding: 1px 8px; }
.st-idle { background: #e7f8ee; color: #067647; }
.st-run { background: #e0efff; color: #175cd3; }
.st-repair { background: #fef0c7; color: #b35b00; }
.st-scrap { background: #f2f4f7; color: #667085; }
.low { color: #d92d20; }

.modal-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 420px; max-height: 86vh; overflow: auto; }
.modal.wide { width: 820px; }
.modal h3 { margin: 0 0 10px; font-size: 16px; }
.modal h4 { margin: 14px 0 6px; font-size: 14px; }
.modal-tip { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.form-row { display: block; margin-bottom: 10px; font-size: 13px; }
.form-row span { display: block; margin-bottom: 4px; color: var(--muted); }
.form-row input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.detail-grid { display: grid; grid-template-columns: 110px 1fr 110px 1fr; gap: 4px 10px; margin: 0; font-size: 13px; }
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.data-table.inner { font-size: 12px; }
.filter-item select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
</style>
