# 素材说明

这个目录放「识别用的图」，按尺寸截好、按角色名命名就行。

```
characters\halfbody\星绘.png     224x320  透明底，取头部到胸口
characters\portraits\星绘.png    128x128  方形头像（播报里补角色用）
characters\lineup\星绘.png       85x52    顶部阵容条那一格
characters\roster\星绘.png       52x34    计分板里的小头像
weapons\icons\...                180x82   武器图标
weapons\strinova-weapons.json             武器名表
weapons\strinova-characters.json          角色名表
```

`profiles\` 是分辨率标定档（1920x1080 基准的 UI 坐标），不用动。
`templates\` 是击杀播报的三个小图标（down / knockdown / kill）。

**这些图片不随仓库分发**：角色立绘和图标是卡拉彼丘（Strinova）的版权素材。
自己从游戏截图里裁一份放进对应目录，程序就能用。缺图时「识别双方阵容」会提示模板目录为空 / 不存在。