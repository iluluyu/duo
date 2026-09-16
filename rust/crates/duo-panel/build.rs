// exe 资源图标（Explorer/任务栏视图）：Windows 目标嵌 assets/duo.ico。
// 交叉编译（windows-gnu）走 windres；纯 Rust 目标零输出。
fn main() {
    let target = std::env::var("TARGET").unwrap_or_default();
    if target.contains("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("../../../assets/duo.ico");
        res.set("FileDescription", "Duo panel");
        res.compile().expect("icon resource compile");
    }
    println!("cargo:rerun-if-changed=../../assets/duo.ico");
}
