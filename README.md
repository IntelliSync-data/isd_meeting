# Meeting Recorder (isd_meeting)

Module này ghi lại cuộc gọi trong **Discuss** của Odoo 18. Bản ghi được lưu trong menu **Meetings**, và có một nút để **AI** viết lời thoại, tóm tắt, việc cần làm và các quyết định.

Module không làm phần mềm họp mới, chỉ thêm vào cuộc gọi sẵn có của Discuss.

- **Phụ thuộc:** `mail`, `bus`, `web`.
- **Không bắt buộc, cài thì dùng thêm được:**
  - `isd_ai_chatbot`: lấy lại API key đã nhập bên đó
  - `isd_credit_app`: tính credit cho mỗi lần nhờ AI

## Trong cuộc gọi

- **Host** là nhân viên vào cuộc gọi **đầu tiên**.
  - Host rời đi thì quyền host chuyển cho người vào sớm nhất còn lại.
  - Khách (guest) và user portal vẫn vào gọi được, nhưng không làm host và không ghi.
- **Nút Ghi** là nút tròn đỏ trên thanh nút của cuộc gọi. Chỉ ai được phép ghi mới thấy nút này. Bấm vào thì chọn:
  - **Video + âm thanh**: ghép camera và màn hình chia sẻ của mọi người thành một khung 1280×720, khoảng 400 MB mỗi giờ
  - **Chỉ âm thanh**: khoảng 15 MB mỗi giờ
- **Đang ghi:**
  - Mọi người trong cuộc gọi thấy nhãn **REC**, tên người ghi và thời gian đã ghi, kể cả người vào sau.
  - Mỗi lúc chỉ có một bản ghi.
- **Dừng ghi** khi xảy ra một trong các việc sau:
  - người ghi bấm nút ■
  - người ghi rời cuộc gọi
  - host dừng bản ghi
  - cuộc gọi kết thúc
- **Cài đặt ghi** (menu ⋮ của cuộc gọi, chỉ host thấy):
  - chọn ai được ghi: **chỉ host**, **mọi người**, hoặc **host và những người được chọn**
  - chuyển quyền host
  - dừng bản ghi của người khác

### Cách ghi

- Bản ghi được làm **trên trình duyệt của người bấm Ghi**. Lý do: video của cuộc gọi Discuss đi thẳng giữa các trình duyệt, không đi qua server.
- Cứ 10 giây trình duyệt gửi một đoạn lên server. Tab bị đóng ngang thì chỉ mất đoạn cuối.
- Trình duyệt không gửi gì quá 2 phút thì cron tự đóng bản ghi.
- **Người ghi nên giữ tab Odoo mở** cho đến khi dừng. Chuyển sang tab khác vẫn ghi được, nhưng video có thể giật.
- Khi ghi video, module ghi thêm một file **chỉ có âm thanh** đi kèm để AI dùng.
- **Trình duyệt:**
  - Chrome và Edge chạy tốt.
  - Firefox ghi được.
  - Safari ra file mp4.

## Menu Meetings

| Màn hình | Nội dung |
|---|---|
| **Meetings** | Mỗi cuộc gọi là một dòng: kênh, giờ, thời lượng, host, số người, số bản ghi, trạng thái AI. Mặc định lọc **Có bản ghi**; bỏ bộ lọc này để xem mọi cuộc gọi |
| **Form cuộc họp** | Tab **Bản ghi** (bấm vào một dòng để xem/nghe ngay, tải về, xoá), **Tóm tắt AI**, **Lời thoại**, **Người tham gia** (giờ vào, giờ ra), **Lịch sử AI** |
| **Recordings** | Mọi bản ghi; nút **Tải về**, **Tải âm thanh**, **Chuyển sang MP4** |
| **Cấu hình → Cấu hình AI** | Xem mục AI bên dưới |

Từ kênh Discuss, nút 🎞 **Cuộc họp & bản ghi** trên đầu kênh mở danh sách cuộc họp của kênh đó.

**Xoá:**
- Không có chuyện tự xoá theo thời gian.
- Xoá cuộc họp thì xoá luôn mọi bản ghi, file và kết quả AI của cuộc họp đó.
- Xoá một bản ghi thì file của bản ghi đó cũng bị xoá.

**Phân quyền:**

| Nhóm | Được làm |
|---|---|
| **Người dùng** (mọi nhân viên) | Xem cuộc họp và bản ghi của các kênh mình là thành viên; xoá bản ghi do mình ghi; bấm Tạo bằng AI; sửa kết quả AI |
| **Quản lý** | Xem và xoá mọi cuộc họp; cấu hình AI |

## AI

1. Vào **Meetings → Cấu hình → Cấu hình AI**:
   - chọn **Gemini** (mặc định) hoặc **OpenAI**
   - nhập API key, hoặc bấm **Dùng key của AI Chatbot**
   - chọn ngôn ngữ kết quả
   - sửa phần hướng dẫn cho AI nếu muốn
2. Trên form cuộc họp, bấm **Tạo bằng AI**.
   - AI chạy nền, mất vài phút; xong sẽ có thông báo.
   - Kết quả gồm **Tóm tắt**, **Việc cần làm**, **Quyết định**, **Lời thoại**, và sửa tay được.
   - Bấm lại thì kết quả mới thay kết quả cũ.

| | Gemini | OpenAI |
|---|---|---|
| Cách làm | Nghe cả file âm thanh, viết mọi thứ trong một lần, cố tách người nói theo tên người tham gia | Chuyển giọng nói thành chữ (`gpt-4o-transcribe` hoặc `whisper-1`), rồi model chat tóm tắt |
| File dài | Được | Trên 25 MB phải có `ffmpeg` để cắt nhỏ |

**isd_credit_app:** vào **Credit → Prices**, đặt giá cho thao tác **Create** trên **Meeting AI Run**. Hết credit thì nút Tạo bằng AI báo lỗi.

## ffmpeg (nên cài trên server)

Không có `ffmpeg` module vẫn chạy. Có `ffmpeg` (`apt install ffmpeg`) thì được thêm:
- **Tua được:** file ghi được sửa lại khi dừng ghi, vì file trình duyệt ghi ra thường không có độ dài.
- **Chuyển sang MP4:** có nút Chuyển sang MP4.
- **Gửi AI nhẹ hơn:** âm thanh được nén còn khoảng 14 MB mỗi giờ trước khi gửi AI; với OpenAI thì được cắt thành từng đoạn 20 phút.

**Nơi lưu file:** thư mục filestore, đường dẫn `filestore/<db>/isd_meeting/<id bản ghi>/`. Backup filestore của Odoo đã bao gồm thư mục này.

## Kiểm thử

```bash
odoo-bin -c odoo.conf -d <db> --db-filter='^<db>$' -i isd_meeting \
  --test-enable --test-tags=/isd_meeting --stop-after-init
```

**16 test:**
- **Host và quyền ghi:** host là người vào đầu, khách không làm host, chuyển host khi host rời, vào lại trong 2 phút vẫn là cuộc họp cũ, ba chế độ quyền ghi, mỗi lúc một bản ghi
- **Nhận file:** nhận từng đoạn đúng thứ tự, gửi lại không bị ghi trùng, đóng bản ghi khi người ghi rời hoặc trình duyệt im lặng, ffmpeg sửa file thật
- **Phân quyền và xoá:** quyền xem theo thành viên kênh, ai được xoá gì, xoá cuộc họp thì xoá file
- **Qua HTTP:** upload từng đoạn, phát lại và tua được (Range)
- **AI:** Gemini, OpenAI (giả lập API), file quá 25 MB khi không có ffmpeg, AI báo lỗi, dùng key của AI Chatbot

Đã chạy thử trên Chrome bằng cuộc gọi thật giữa 2 người, một người bật camera:
- ghi được video có cả hai khung hình và tiếng
- người thứ hai thấy nhãn REC
- host đổi quyền thì nút Ghi hiện ra ở người kia
- file phát lại được trong form bản ghi
