# isd_meeting: Yêu cầu chức năng (bản 1)

> Trạng thái: **đã duyệt** (23/11/2026), đã làm xong bản đầu (xem README.md).
> Nền tảng: Odoo 18, LGPL-3, category **ISD Modules**, tác giả IntelliSyncData.
> Không làm phần mềm họp mới. Module chỉ **mở rộng cuộc gọi của Discuss** (mail).

---

## 1. Mục tiêu

- Trong cuộc gọi Discuss có nút **Ghi**. Người ghi chọn **ghi video** hoặc **chỉ ghi âm**.
- Khi dừng ghi hoặc khi cuộc gọi kết thúc, bản ghi được lưu vào Odoo.
- Menu **Meetings** quản lý các cuộc họp và bản ghi, có nút nhờ **AI** viết bản ghi lời nói và tóm tắt.

---

## 2. Khái niệm

| Từ | Nghĩa |
|---|---|
| **Meeting** | Một cuộc gọi trên một kênh, tính từ lúc người đầu tiên vào đến lúc người cuối cùng rời |
| **Bản ghi** | Một lần bấm Ghi rồi Dừng. Một meeting có thể có nhiều bản ghi |
| **Host** | Người quyết định ai được ghi trong meeting đó |

### Ai là host
Discuss không có vai trò chủ phòng: trong kênh ai cũng ngang nhau. Vì vậy:
- **Người vào cuộc gọi đầu tiên** là host. Chỉ tính **nhân viên** (user nội bộ); khách (guest) và user portal vẫn vào cuộc gọi được nhưng không làm host và không ghi.
- Host rời cuộc gọi thì quyền host chuyển cho người **vào sớm nhất** trong số người còn lại.
- Host có thể **chuyển quyền host** cho người khác đang trong cuộc gọi.

### Host chọn ai được ghi
Host chọn một trong ba chế độ ngay trong cuộc gọi. Mặc định là **Chỉ host**.

| Chế độ | Ai được bấm Ghi |
|---|---|
| **Chỉ host** | Chỉ host |
| **Mọi người** | Ai trong cuộc gọi cũng được |
| **Người được cho phép** | Host và những người host đã tick chọn |

---

## 3. Trong cuộc gọi

- **Nút Ghi** nằm trên thanh nút của cuộc gọi, cạnh mic, camera và chia sẻ màn hình. Ai không có quyền ghi thì không thấy nút.
- Bấm Ghi thì chọn **Video + âm thanh** hoặc **Chỉ âm thanh**.
- **Đang ghi:**
  - Mọi người trong cuộc gọi thấy **chấm đỏ "Đang ghi"**, kèm tên người ghi và thời gian đã ghi.
  - Người vào sau cũng thấy chấm đỏ này.
- **Dừng ghi:**
  - Người ghi tự bấm **Dừng**, hoặc
  - Người ghi rời cuộc gọi hoặc đóng tab, hoặc
  - Cuộc gọi kết thúc.
- **Mỗi lúc chỉ có một bản ghi.** Có người đang ghi thì nút Ghi của người khác bị khoá.
- **Menu host** (trong thanh nút):
  - chọn chế độ ghi
  - tick người được phép ghi
  - chuyển quyền host
  - **dừng bản ghi của người khác**

### Cách ghi
- Ghi **trên trình duyệt của người bấm Ghi**, vì luồng video của cuộc gọi Discuss đi giữa các trình duyệt chứ không qua server Odoo.
- **Âm thanh:** trộn tiếng của mọi người, kể cả người ghi.
- **Video:** ghép camera của mọi người thành dạng lưới. Khi có người chia sẻ màn hình thì màn hình đó hiện lớn, camera thu nhỏ bên cạnh. Mặc định 720p.
- Khi ghi video, module **ghi thêm một file chỉ có âm thanh** đi kèm, để AI dùng sau này. File này nhẹ, khoảng 15–30 MB mỗi giờ.
- Cứ khoảng **10 giây** đẩy một đoạn lên server. Trình duyệt bị tắt ngang thì vẫn giữ được phần đã ghi. Khi bản ghi kết thúc, server ghép các đoạn thành một file.
- File ra định dạng **`.webm`**. Nếu server có cài `ffmpeg` thì có thêm tuỳ chọn chuyển sang **`.mp4`**.

---

## 4. Menu Meetings

| Màn hình | Nội dung |
|---|---|
| **Danh sách meeting** | Kênh, ngày giờ, thời lượng, host, số người tham gia, số bản ghi, đã có tóm tắt AI hay chưa. Tìm và lọc theo kênh, ngày, người tham gia; nhóm theo kênh hoặc tháng |
| **Form meeting** | Tên (tự đặt theo kênh và ngày, sửa được); host; người tham gia kèm giờ vào và giờ ra. Tab **Bản ghi**: phát video hoặc âm thanh ngay trong Odoo, tải về, xoá từng bản ghi. Tab **AI**: bản ghi lời nói, tóm tắt, việc cần làm |
| **Cấu hình AI** | Xem mục 5 |

Trên kênh Discuss có nút xem các meeting của kênh đó.

### Xoá
- **Không tự xoá theo thời gian.** Người dùng tự xoá.
- **Xoá meeting thì xoá luôn** mọi bản ghi, file trên filestore và kết quả AI của meeting đó.
- Xoá riêng một bản ghi thì xoá file của bản ghi đó.

### Ai được xem
| Nhóm | Được làm |
|---|---|
| **Người dùng** | Xem meeting và bản ghi của các kênh mình là thành viên; xoá bản ghi do mình ghi |
| **Meeting Manager** | Xem và xoá mọi meeting; cấu hình AI |

---

## 5. AI: bản ghi lời nói và tóm tắt

- AI **không chạy tự động.** Trên form meeting có nút **"Tạo bằng AI"**; chạy lại được, kết quả mới ghi đè kết quả cũ.
- **Kết quả AI gồm:**
  - **bản ghi lời nói** (có giờ, cố gắng tách theo người nói)
  - **tóm tắt**
  - **việc cần làm**
  - các **quyết định** đã chốt trong buổi họp
- Kết quả **sửa tay được.**
- Chạy **nền**, vì một buổi họp dài cần vài phút. Có trạng thái *Đang chạy / Xong / Lỗi kèm lý do*. Chạy xong thì báo cho người bấm.
- AI dùng **file âm thanh** của các bản ghi trong meeting, ghép theo thứ tự thời gian.

### Cấu hình AI (menu Meetings → Cấu hình)
- **Nhà cung cấp:**
  - **Gemini**: mặc định, nghe được file âm thanh dài trong một lần gọi
  - **OpenAI**: chuyển giọng nói thành chữ bằng model transcribe, rồi tóm tắt bằng model chat. File quá 25 MB thì phải cắt, nên **cần `ffmpeg`** trên server
- **API Key**: chỉ Manager đọc được.
- **Model**: một ô cho Gemini; với OpenAI là hai ô, model transcribe và model chat.
- **Ngôn ngữ kết quả**: Tiếng Việt hoặc English.
- **Prompt tóm tắt**: có sẵn mẫu, sửa được; ví dụ muốn có thêm mục "Rủi ro".
- Nếu đã cài **isd_ai_chatbot** thì có nút **Dùng key của AI Chatbot**, để không phải nhập lại key. Module vẫn **không bắt buộc** cài isd_ai_chatbot.
- Có cài **isd_credit_app** thì tính credit được. Gợi ý: mỗi lần bấm "Tạo bằng AI" tính credit, đặt giá trong Prices như omnichat.

---

## 6. Kỹ thuật (tóm tắt)

- **Phụ thuộc:** `mail`, `bus`, `web`.
- **Model:**
  - `isd.meeting`: một cuộc gọi. Gắn với `discuss.channel`, có host, chế độ ghi, giờ bắt đầu và kết thúc
  - `isd.meeting.attendee`: người tham gia kèm giờ vào và giờ ra, lấy từ `discuss.channel.rtc.session`
  - `isd.meeting.recording`: bản ghi. Có loại (video / âm thanh), người ghi, thời lượng, file video, file âm thanh, trạng thái (đang ghi / xong / lỗi). Xoá theo meeting (`ondelete='cascade'`) và xoá luôn file đính kèm
  - Kết quả AI lưu trên `isd.meeting`
- **Phía trình duyệt:**
  - Patch nút cuộc gọi của mail (`callActionsRegistry`) và service RTC.
  - Ghi bằng `MediaRecorder`, trộn âm bằng `AudioContext`, ghép video bằng `canvas`.
  - Mỗi đoạn ghi gửi lên một route upload, có kiểm tra người gửi đúng là người đang ghi.
- **Thời gian thực:** trạng thái "Đang ghi", host và chế độ ghi đẩy cho mọi người qua `bus`.
- **Lưu file:** `ir.attachment` (filestore). Ước tính video 720p khoảng **300–500 MB mỗi giờ**.
- **Kiểm thử:**
  - chọn host và chuyển host khi host rời
  - ba chế độ quyền ghi; mỗi lúc chỉ có một bản ghi
  - upload đoạn ghi, ghép file, kết thúc bản ghi khi người ghi rời
  - quyền xem theo thành viên kênh
  - xoá meeting thì xoá bản ghi và file
  - AI chạy với API giả lập, cả Gemini và OpenAI
  - test trình duyệt: nút Ghi hiện và ẩn theo quyền

---

## 7. Giới hạn

- **Người ghi phải ở lại trong cuộc gọi.** Họ rời đi thì dừng ghi; phần đã ghi vẫn còn, và người khác có quyền thì bấm ghi tiếp, thành bản ghi mới.
- **Chất lượng bản ghi phụ thuộc máy và mạng của người ghi.** Ghép video nhiều người tốn CPU.
- **Trình duyệt:**
  - Chrome và Edge chạy tốt.
  - Firefox ghi được.
  - Safari ghi được nhưng ra file `.mp4`.
- Ghi bằng "bot" trên server, tức là ghi được cả khi không ai bấm, **không làm trong bản này** vì phải dựng SFU riêng và chạy trình duyệt ẩn trên server.
