// GUI 子系统：不闪控制台（duo-core 是 CLI 保持 console；面板是窗口进程）。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    duo_panel::run();
}
