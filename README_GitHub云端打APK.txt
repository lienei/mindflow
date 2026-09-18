MindFlow Android v0.2 —— GitHub 云端自动生成 APK

这个版本不需要你在 Windows 安装 Ubuntu、WSL、Android Studio 或 Buildozer。
GitHub 云端服务器会自动完成 APK 编译。

==============================
第一次使用：只需要做这些
==============================

1. 打开 https://github.com 并登录。

2. 点击右上角 “+” → “New repository”。

3. Repository name 可以填写：
   MindFlow-Android

4. 选择 Public 或 Private 都可以。
   然后点击 “Create repository”。

5. 在新仓库页面点击：
   “uploading an existing file”
   或 “Add file” → “Upload files”。

6. 把本文件夹里的全部内容上传到仓库根目录。

   上传完成后，仓库根目录应该能直接看到：
   main.py
   buildozer.spec

   同时还必须存在：
   .github/workflows/build-apk.yml

7. 点击 “Commit changes”。

==============================
APK 会自动开始生成
==============================

提交后：

GitHub 仓库顶部 → Actions
→ Build MindFlow Android APK
→ 点击最新的一次运行

第一次编译通常会比较慢，因为服务器需要下载 Android SDK/NDK 等环境。

当页面显示绿色对勾后：

在该运行页面最下方找到 Artifacts
→ MindFlow-Android-APK
→ 点击下载

下载得到的是一个 ZIP。
解压 ZIP 后，里面就是 .apk 文件。

把 APK 发到 Android 手机：
→ 点击 APK
→ 允许“安装未知应用”
→ 安装

==============================
以后修改程序
==============================

以后只要修改 main.py 或 buildozer.spec 并上传/提交，
GitHub 会自动重新编译新的 APK。

如果不想改代码、只是想手动重新打一次：

GitHub → Actions
→ Build MindFlow Android APK
→ Run workflow
→ Run workflow

==============================
目前 Android v0.2
==============================

已有：
- 思维导图显示
- 触摸选择节点
- 拖动画布
- 双指缩放
- 添加子节点
- 添加同级节点
- 编辑文字
- 删除节点
- 折叠/展开
- 背诵进度
- 超链接
- 节点内嵌图片显示
- .mflow v2 数据结构
- App 内部自动保存

后续再逐步加入：
- 从手机相册插入图片
- 手机文件选择/打开 .mflow
- 导入 ITMZ
- Anki 导入导出
- AI 资料导入
- 与 Windows 版更完整的文件互通
