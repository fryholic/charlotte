class AudioScheduler:
    def __init__(self):
        self.queues = []
        self.text_channel = None
        self.playing_message = None
        return

    def enqueue(self, track):
        self.queues.append(track)
        print(f"🎵 대기열 추가: {track.title}")
        return track

    def enqueue_list(self, tracks):
        self.queues.extend(tracks)
        print(f"🎵 대기열 추가: {len(tracks)}곡")
        return tracks

    def clear(self):
        self.queues.clear()
        print("🎵 대기열 초기화")
        return

    def dequeue(self):
        if not self.is_empty():
            removed = self.queues.pop(0)
            print(f"🎵 대기열 삭제: {removed.title}")
            return removed
        return None

    def clone(self):
        return self.queues.copy()

    def is_empty(self):
        return len(self.queues) == 0

    def __len__(self):
        return len(self.queues)

    def __iter__(self):
        return iter(self.queues)

    def __del__(self):
        self.clear()
        print("🔚 오디오 스케줄러 삭제")
        return
