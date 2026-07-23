import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd"
import { Plus, Trash2, XCircle } from "lucide-react"
import { ReactNode, useEffect, useMemo, useState } from "react"

import type { Task } from "./api/taskhub"
import { cancelTask, confirmTask, getTask } from "./api/taskhub"
import { taskLabel } from "./intentDrafts"
import {
  ConfirmationDraft,
  ConfirmOptions,
  MAX_ACCEPTANCE_CRITERIA,
  buildTaskConfirmationRequests,
  cancelTasksSequentially,
  confirmTaskRequestsSequentially,
  confirmationTaskIdsToCancelOnClose,
  confirmationDraftFromTask,
  validateConfirmationDraft,
} from "./taskConfirmation"


interface TaskConfirmationModalProps {
  open: boolean
  tasks: Task[]
  preparing?: boolean
  preparationError?: string
  title?: string
  intro?: string
  beforeTasks?: ReactNode
  confirmOptions?: ConfirmOptions
  cancelOnClose?: boolean
  onTaskUpdated: (task: Task) => void | Promise<void>
  onTasksCancelled?: (taskIds: string[]) => void | Promise<void>
  onClose: () => void
}

export function TaskConfirmationModal({
  open,
  tasks,
  preparing = false,
  preparationError = "",
  title = "确认任务契约",
  intro = "请确认任务目标、交付物和验收标准，确认后系统会异步执行。",
  beforeTasks,
  confirmOptions = { execution_mode: "async" },
  cancelOnClose = true,
  onTaskUpdated,
  onTasksCancelled,
  onClose,
}: TaskConfirmationModalProps) {
  const taskIdsKey = tasks.map((task) => task.id).join("\u0000")
  const [drafts, setDrafts] = useState<Record<string, ConfirmationDraft>>({})
  const [remainingTaskIds, setRemainingTaskIds] = useState<string[]>([])
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState("")
  const activeTasks = useMemo(
    () => tasks.filter((task) => remainingTaskIds.includes(task.id)),
    [tasks, remainingTaskIds],
  )

  useEffect(() => {
    if (!open) return
    setDrafts(Object.fromEntries(
      tasks.map((task) => [task.id, confirmationDraftFromTask(task)]),
    ))
    setRemainingTaskIds(tasks.map((task) => task.id))
    setError("")
  }, [open, taskIdsKey])

  function updateDraft(taskId: string, patch: Partial<ConfirmationDraft>) {
    setDrafts((current) => {
      const task = tasks.find((item) => item.id === taskId) || { id: taskId }
      return {
        ...current,
        [taskId]: {
          ...(current[taskId] || confirmationDraftFromTask(task)),
          ...patch,
        },
      }
    })
    setError("")
  }

  function updateList(taskId: string, index: number, value: string) {
    const draft = drafts[taskId]
    if (!draft) return
    const next = [...draft.successCriteria]
    next[index] = value
    updateDraft(taskId, { successCriteria: next })
  }

  function addListItem(taskId: string) {
    const draft = drafts[taskId]
    if (!draft) return
    if (draft.successCriteria.length >= MAX_ACCEPTANCE_CRITERIA) return
    updateDraft(taskId, { successCriteria: [...draft.successCriteria, ""] })
  }

  function removeListItem(taskId: string, index: number) {
    const draft = drafts[taskId]
    if (!draft) return
    updateDraft(taskId, {
      successCriteria: draft.successCriteria.filter((_, itemIndex) => itemIndex !== index),
    })
  }

  async function submit() {
    const validationMessages = activeTasks.flatMap((task) => {
      const draft = drafts[task.id] || confirmationDraftFromTask(task)
      return validateConfirmationDraft(draft).map(
        (message) => `${draft.title || task.id}：${message}`,
      )
    })
    if (validationMessages.length) {
      setError(validationMessages.join("；"))
      return
    }

    setConfirming(true)
    setError("")
    try {
      await confirmTaskRequestsSequentially(
        buildTaskConfirmationRequests(activeTasks, drafts, confirmOptions),
        confirmTask,
        getTask,
        async (confirmed) => {
          setRemainingTaskIds((current) => current.filter((taskId) => taskId !== confirmed.id))
          setDrafts((current) => {
            const next = { ...current }
            delete next[confirmed.id]
            return next
          })
          await onTaskUpdated(confirmed)
        },
      )
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败")
    } finally {
      setConfirming(false)
    }
  }

  async function close() {
    if (preparing || confirming) return
    const taskIds = confirmationTaskIdsToCancelOnClose(tasks, remainingTaskIds, cancelOnClose)
    if (taskIds.length) {
      setConfirming(true)
      setError("")
      try {
        await cancelTasksSequentially(
          taskIds,
          cancelTask,
          getTask,
          async (taskId) => {
            setRemainingTaskIds((current) => current.filter((currentId) => currentId !== taskId))
            setDrafts((current) => {
              const next = { ...current }
              delete next[taskId]
              return next
            })
            await onTasksCancelled?.([taskId])
          },
        )
      } catch (err) {
        setError(err instanceof Error ? err.message : "取消任务失败")
        setConfirming(false)
        return
      }
      setConfirming(false)
    }
    onClose()
  }

  return (
    <Modal
      title={title}
      open={open}
      width={860}
      onCancel={() => void close()}
      footer={preparing || preparationError ? null : [
        <Button key="cancel" onClick={() => void close()} disabled={confirming}>取消</Button>,
        <Button
          key="confirm"
          type="primary"
          onClick={() => void submit()}
          loading={confirming}
          disabled={activeTasks.length === 0}
        >
          确认并执行
        </Button>,
      ]}
      mask={{ closable: false }}
      closable={!preparing && !confirming}
    >
      <Typography.Paragraph type="secondary">
        {preparing ? "正在拆分整理任务清单，请稍后" : intro}
      </Typography.Paragraph>
      {!preparing && !preparationError && beforeTasks}
      {preparing ? (
        <div className="intent-loading">
          <Spin size="large" />
          <strong>正在拆分整理任务清单，请稍后</strong>
          <span>系统正在调用意图识别能力，返回后会在这里展示待确认任务。</span>
        </div>
      ) : preparationError ? (
        <div className="intent-loading error">
          <XCircle size={34} />
          <strong>任务清单整理失败</strong>
          <span>{preparationError}</span>
        </div>
      ) : (
        <>
          {error && (
            <Alert
              type="error"
              showIcon
              title="请完善确认信息"
              description={error}
              className="confirmation-error"
            />
          )}
          <div className="intent-task-list">
            {activeTasks.map((task) => {
              const draft = drafts[task.id] || confirmationDraftFromTask(task)
              return (
                <Card className="intent-task-card confirmation-task-card" key={task.id} size="small">
                  <div className="intent-task-index">
                    <Tag color="blue">{taskLabel()}</Tag>
                    <Typography.Text type="secondary">目标与验收信息均可编辑</Typography.Text>
                  </div>
                  <div className="confirmation-fields">
                    <label className="field">
                      <span>任务名称</span>
                      <Input
                        value={draft.title}
                        onChange={(event) => updateDraft(task.id, { title: event.target.value })}
                      />
                    </label>
                    <label className="field">
                      <span>任务描述</span>
                      <Input.TextArea
                        rows={2}
                        value={draft.description}
                        onChange={(event) => updateDraft(task.id, { description: event.target.value })}
                      />
                    </label>
                    <label className="field confirmation-wide-field">
                      <span>任务目标</span>
                      <Input.TextArea
                        rows={4}
                        value={draft.goal}
                        onChange={(event) => updateDraft(task.id, { goal: event.target.value })}
                      />
                    </label>
                    <label className="field confirmation-wide-field">
                      <span>交付物目标</span>
                      <Input.TextArea
                        rows={4}
                        value={draft.deliverableGoal}
                        onChange={(event) => updateDraft(task.id, { deliverableGoal: event.target.value })}
                      />
                    </label>
                    <ConfirmationListField
                      title="验收标准"
                      addLabel="增加"
                      deleteLabel="删除验收标准"
                      placeholder="例如：方案包含实施步骤、风险说明，并可直接评审"
                      values={draft.successCriteria}
                      maxItems={MAX_ACCEPTANCE_CRITERIA}
                      onAdd={() => addListItem(task.id)}
                      onChange={(index, value) => updateList(task.id, index, value)}
                      onRemove={(index) => removeListItem(task.id, index)}
                    />
                  </div>
                </Card>
              )
            })}
          </div>
        </>
      )}
    </Modal>
  )
}

function ConfirmationListField({
  title,
  emptyText,
  addLabel,
  deleteLabel,
  placeholder,
  values,
  maxItems,
  onAdd,
  onChange,
  onRemove,
}: {
  title: string
  emptyText?: string
  addLabel: string
  deleteLabel: string
  placeholder: string
  values: string[]
  maxItems?: number
  onAdd: () => void
  onChange: (index: number, value: string) => void
  onRemove: (index: number) => void
}) {
  return (
    <div className="confirmation-list-field">
      <div className="confirmation-list-heading">
        <span>{title}</span>
        <Button
          size="small"
          type="text"
          icon={<Plus size={14} />}
          disabled={maxItems !== undefined && values.length >= maxItems}
          onClick={onAdd}
        >
          {addLabel}
        </Button>
      </div>
      <div className="confirmation-list-items">
        {values.map((value, index) => (
          <div className="confirmation-list-row" key={`${title}-${index}`}>
            <Input
              value={value}
              placeholder={placeholder}
              onChange={(event) => onChange(index, event.target.value)}
            />
            <Tooltip title={deleteLabel}>
              <Button
                size="small"
                type="text"
                danger
                aria-label={deleteLabel}
                icon={<Trash2 size={14} />}
                onClick={() => onRemove(index)}
              />
            </Tooltip>
          </div>
        ))}
        {!values.length && emptyText && (
          <span className="confirmation-list-empty">{emptyText}</span>
        )}
      </div>
    </div>
  )
}
