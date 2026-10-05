# CPU COOLER V4.0 Linux Sender

Catatan ini menjelaskan cara menjalankan cooler/display USB **CPU COOLER V4.0** di Linux, terutama CachyOS atau distro berbasis Arch Linux.

## Ringkasan Reverse Engineering

Installer Windows `CPU COOLER V4.0 Setup 250418.exe` berisi aplikasi .NET/WinForms. Aplikasi tersebut membaca sensor komputer, lalu mengirim data ke device USB HID.

Detail yang ditemukan:

- Device memakai HID, bukan serial COM.
- Target device: `VID_AA88` dan `PID_8666`.
- Program Windows utama menulis 11 byte ke named pipe `tifPipe`.
- Program kecil `SendTemp.exe` memakai buffer 24 byte, tetapi payload aktif yang terbukti dipakai device adalah 11 byte.

Format report HID:

| Byte | Isi |
| ---: | --- |
| 0 | Report ID / prefix, biasanya `0` |
| 1 | CPU temperature, Celsius |
| 2 | GPU temperature, Celsius |
| 3 | CPU usage, percent |
| 4 | Memory usage, percent |
| 5 | Motherboard/board temperature, Celsius |
| 6 | GPU usage, percent |
| 7 | Fan RPM high byte |
| 8 | Fan RPM low byte |
| 9 | CPU power high byte |
| 10 | CPU power low byte |
| 11+ | Padding hanya jika firmware/report length lebih panjang |

Script Linux di folder ini langsung menulis report ke `/dev/hidrawX`, jadi tidak membutuhkan software Windows.

## File

- `cpu_cooler_v4_linux.py`: sender Linux untuk HID device.
- `99-cpu-cooler-v4.rules`: udev rule agar user biasa bisa mengakses device tanpa `sudo`.

## Kebutuhan

Di CachyOS / Arch Linux:

```bash
sudo pacman -S python lm_sensors
```

Opsional, untuk sensor hardware yang lebih lengkap:

```bash
sudo sensors-detect
sensors
```

Untuk GPU NVIDIA, script akan memakai `nvidia-smi` jika tersedia.

## Cek Device

Colok cooler, lalu cek:

```bash
lsusb | grep -i aa88
ls /dev/hidraw*
```

Kalau `lsusb` menampilkan device dengan ID `aa88:8666`, device sudah terdeteksi kernel Linux.

Untuk melihat hidraw mana yang cocok:

```bash
for d in /sys/class/hidraw/hidraw*; do
  echo "$d"
  cat "$d/device/uevent"
done
```

Cari baris yang mengandung `0003:0000AA88:00008666` atau pola mirip `AA88:8666`.

## Test Tanpa Menulis ke Device

Dari root project:

```bash
python linux/cpu_cooler_v4_linux.py --dry-run --once
```

Output akan berupa 11 byte dalam hex. Contoh bentuknya:

```text
00 41 00 15 37 00 00 03 e8 00 2d
```

## Jalankan Sekali

Kalau permission belum disiapkan:

```bash
sudo python linux/cpu_cooler_v4_linux.py --once
```

Kalau berhasil, display/cooler harus menerima satu update.

## Jalankan Terus

```bash
sudo python linux/cpu_cooler_v4_linux.py
```

Default interval update adalah 1 detik. Untuk mengganti interval:

```bash
sudo python linux/cpu_cooler_v4_linux.py --interval 2
```

## Menentukan Hidraw Manual

Kalau auto-detect gagal tapi kamu tahu path device:

```bash
sudo python linux/cpu_cooler_v4_linux.py --device /dev/hidraw3
```

Ganti `/dev/hidraw3` sesuai device di sistem kamu.

## Udev Rule Tanpa Sudo

Install rule:

```bash
sudo cp linux/99-cpu-cooler-v4.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Tambahkan user ke group `plugdev`:

```bash
sudo groupadd -f plugdev
sudo usermod -aG plugdev "$USER"
```

Logout lalu login lagi. Setelah itu coba:

```bash
python linux/cpu_cooler_v4_linux.py --once
```

Catatan: beberapa distro Arch-based tidak memakai group `plugdev` secara default. Kalau ingin pakai group lain, edit `99-cpu-cooler-v4.rules`, misalnya `GROUP="users"` atau group khusus yang kamu buat.

## Autostart Dengan Systemd User Service

Buat folder service:

```bash
mkdir -p ~/.config/systemd/user
```

Buat file:

```bash
nano ~/.config/systemd/user/cpu-cooler-v4.service
```

Isi contoh:

```ini
[Unit]
Description=CPU COOLER V4.0 Linux sender
After=default.target

[Service]
Type=simple
WorkingDirectory=/path/ke/project
ExecStart=/usr/bin/python /path/ke/project/linux/cpu_cooler_v4_linux.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

Ganti `/path/ke/project` ke lokasi project kamu.

Aktifkan:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cpu-cooler-v4.service
systemctl --user status cpu-cooler-v4.service
```

Kalau ingin service tetap jalan setelah login user aktif, ini cukup. Kalau ingin berjalan sebelum login desktop, sebaiknya pakai system service dan pastikan permission hidraw benar.

## Troubleshooting

### Device tidak ditemukan

Jalankan:

```bash
lsusb
```

Kalau tidak ada `aa88:8666`, kemungkinan:

- Device belum dicolok.
- Kabel/port bermasalah.
- Device kamu memakai VID/PID berbeda.

Kalau VID/PID berbeda, jalankan script dengan edit konstanta `VID` dan `PID` di `cpu_cooler_v4_linux.py`.

### Permission denied

Gunakan `sudo` untuk test cepat:

```bash
sudo python linux/cpu_cooler_v4_linux.py --once
```

Untuk solusi permanen, pasang udev rule seperti bagian "Udev Rule Tanpa Sudo".

### CPU temp terbaca 0

Install dan konfigurasi sensor:

```bash
sudo pacman -S lm_sensors
sudo sensors-detect
sensors
```

Beberapa sensor CPU/GPU tergantung kernel module dan hardware.

### GPU temp atau usage terbaca 0

Untuk NVIDIA, pastikan `nvidia-smi` tersedia:

```bash
nvidia-smi
```

Untuk AMD/Intel, script mencoba membaca dari `/sys/class/drm` dan `/sys/class/hwmon`. Tidak semua driver mengekspos field yang sama.

### Fan RPM terbaca 0

Script membaca `fan*_input` dari `/sys/class/hwmon`. Kalau motherboard tidak mengekspos RPM ke Linux, nilainya akan 0. Jalankan:

```bash
find /sys/class/hwmon -name 'fan*_input' -print -exec cat {} \;
```

### Display tidak berubah walau script berjalan

Coba kirim paket manual:

```bash
sudo python linux/cpu_cooler_v4_linux.py --once
```

Lalu cek apakah hidraw yang dipakai benar:

```bash
for d in /sys/class/hidraw/hidraw*; do
  echo "$d"
  cat "$d/device/uevent"
done
```

Jika device punya VID/PID benar tapi tetap tidak berubah, kemungkinan report HID membutuhkan panjang berbeda pada firmware tertentu. Test langsung di Windows menunjukkan device menerima 11 byte, sedangkan helper vendor memakai buffer pipe 24 byte.

## Catatan Keamanan

Script ini hanya menulis data sensor ke USB HID device dengan VID/PID yang cocok. Tetap hindari menjalankan binary Windows dari vendor tanpa perlu. Untuk debugging, lebih aman memakai ekstraksi offline dan membaca HID traffic seperti yang dilakukan di project ini.
