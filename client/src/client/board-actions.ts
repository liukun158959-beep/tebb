import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { rpc, type BackendInfo } from "./api";
import type { BoardPayload } from "./board";

// Own board request ordering and form/error state; backend owns remote/cache stages.
export function useBoardActions({ info, boardClock, setBoard, onLoaded }: {
  info: BackendInfo | null;
  boardClock: string | null;
  setBoard: Dispatch<SetStateAction<BoardPayload | null>>;
  onLoaded: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [rowDeleting, setWriting] = useState(false);
  const writing = useRef(false);
  const revision = useRef(0);
  const [agendaErrors, setAgendaErrors] = useState<Record<string, string>>({});
  const [taskErrors, setTaskErrors] = useState<Record<string, string>>({});
  const [createError, setCreateError] = useState("");
  const [createNotice, setCreateNotice] = useState("");
  const [createNonce, setCreateNonce] = useState(0);

  async function request<T extends { ok?: boolean; error?: string }>(method: string, args: Record<string, unknown>) {
    if (!info) throw new Error("还没有连上本地后端，请重新连接后核对结果。");
    const data = await rpc<T>(info, method, args);
    if (!data || data.ok !== true) throw new Error(data?.error || "看板操作未完整完成，请先核对结果。");
    return data;
  }

  async function loadBoardSnapshot(refresh: boolean) {
    const current = ++revision.current;
    const duringWrite = writing.current;
    setLoading(true);
    try {
      const data = await request<BoardPayload>("load_board", refresh ? { refresh: true } : {});
      if (current !== revision.current || duringWrite) return;
      onLoaded();
      setBoard(data);
      setLoadError("");
      setAgendaErrors({});
      setTaskErrors({});
      setCreateError("");
      setCreateNotice("");
    } catch (err) {
      if (current === revision.current && !duringWrite) setLoadError(String(err));
    } finally {
      if (current === revision.current) setLoading(false);
    }
  }

  async function deleteAgenda(eventId: string) {
    if (!eventId || writing.current) return;
    writing.current = true;
    setLoading(false);
    setWriting(true);
    revision.current += 1;
    setAgendaErrors((cur) => {
      const next = { ...cur };
      delete next[eventId];
      return next;
    });
    try {
      if (boardClock !== null) {
        setBoard((cur) => {
          if (!cur?.agenda?.items) return cur;
          return {
            ...cur,
            agenda: {
              ...cur.agenda,
              items: cur.agenda.items.filter((item) => item.event_id !== eventId),
            },
          };
        });
        return;
      }
      if (!info) {
        setAgendaErrors((cur) => ({ ...cur, [eventId]: "还没有连上本地后端。恢复：重启客户端。" }));
        return;
      }
      const data = await request<BoardPayload>("delete_agenda", { event_id: eventId });
      setBoard(data);
    } catch (err) {
      setAgendaErrors((cur) => ({ ...cur, [eventId]: String(err) }));
    } finally {
      revision.current += 1;
      setLoading(false);
      writing.current = false;
      setWriting(false);
    }
  }

  async function deleteTask(guid: string) {
    if (!guid || writing.current) return;
    writing.current = true;
    setLoading(false);
    setWriting(true);
    revision.current += 1;
    setTaskErrors((cur) => {
      const next = { ...cur };
      delete next[guid];
      return next;
    });
    try {
      if (boardClock !== null) {
        setBoard((cur) => {
          if (!cur?.tasks?.items) return cur;
          return {
            ...cur,
            tasks: {
              ...cur.tasks,
              items: cur.tasks.items.filter((item) => item.guid !== guid),
            },
          };
        });
        return;
      }
      if (!info) {
        setTaskErrors((cur) => ({ ...cur, [guid]: "还没有连上本地后端。恢复：重启客户端。" }));
        return;
      }
      const data = await request<BoardPayload>("delete_task", { guid });
      setBoard(data);
    } catch (err) {
      setTaskErrors((cur) => ({ ...cur, [guid]: String(err) }));
    } finally {
      revision.current += 1;
      setLoading(false);
      writing.current = false;
      setWriting(false);
    }
  }

  async function createEvent(summary: string, start: string, end: string) {
    if (writing.current) return;
    writing.current = true;
    setLoading(false);
    setWriting(true);
    revision.current += 1;
    setCreateError("");
    setCreateNotice("");
    try {
      if (boardClock !== null) {
        const boardDay = boardClock.slice(0, 10);
        if (start.slice(0, 10) !== boardDay) {
          setCreateNotice("已创建。今天的看板只显示今天。");
          setCreateNonce((n) => n + 1);
          return;
        }
        const clock = (iso: string) => iso.slice(11, 16);
        setBoard((cur) => {
          if (!cur?.agenda) return cur;
          return {
            ...cur,
            agenda: {
              ...cur.agenda,
              ok: true,
              items: [
                ...(cur.agenda.items || []),
                { summary, start: clock(start), end: clock(end), event_id: `evt-${start}` },
              ],
            },
          };
        });
        setCreateNonce((n) => n + 1);
        return;
      }
      if (!info) {
        setCreateError("还没有连上本地后端。恢复：重启客户端。");
        return;
      }
      const data = await request<{ ok?: boolean; error?: string; notice?: string; snapshot?: BoardPayload | null }>("create_agenda", {
        summary,
        start,
        end,
      });
      if (data.snapshot) setBoard(data.snapshot);
      setCreateNotice(data.notice || "");
      setCreateNonce((n) => n + 1);
    } catch (err) {
      setCreateError(String(err));
    } finally {
      revision.current += 1;
      setLoading(false);
      writing.current = false;
      setWriting(false);
    }
  }

  return { loading, loadError, rowDeleting, agendaErrors, taskErrors,
    createError, createNotice, createNonce, loadBoardSnapshot, deleteAgenda, deleteTask, createEvent };
}
