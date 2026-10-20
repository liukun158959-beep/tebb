"""Today's board RPC results, independent of GUI and model lifecycles."""
from . import board_data


class BoardService:
    def _call(self, action):
        try:
            return action()
        except board_data.BoardChangeError as exc:
            return dict(ok=False, error=str(exc), **exc.details)
        except Exception as exc:
            return dict(ok=False, change_state='not_changed', stage='validation',
                        remote_state='not_started', error=str(exc))

    def load_board(self, refresh=False):
        return self._call(lambda: dict(ok=True, **board_data.load_today_snapshot(refresh=bool(refresh))))

    def create_agenda(self, summary, start, end):
        return self._call(lambda: board_data.create_board_event(summary, start, end))

    def delete_agenda(self, event_id):
        return self._call(lambda: board_data.delete_agenda_event(event_id))

    def delete_task(self, guid):
        return self._call(lambda: board_data.delete_board_task(guid))
