# KaanhPilot 协作开发规范

本文约定团队从首次获取项目到日常开发、测试、提交、Pull Request（PR）、合并和清理分支的完整流程。命令以 Windows PowerShell 为例，在项目根目录执行；分支名和文件路径请替换为实际值。

## 1. 基本规定

- `master` 是稳定主分支。日常修改在任务分支完成，通过 PR 合并到 `master`。
- 每个任务使用独立分支，每个 PR 尽量只解决一个问题。分支在合并后删除，不反复复用。
- 开始新任务前，先更新本地 `master`，再创建分支。

### 分支命名

使用小写英文，以连字符连接单词，按用途添加前缀：

| 类型 | 前缀 | 示例 |
| --- | --- | --- |
| 新功能 | `feature/` | `feature/xxx` |
| Bug 修复 | `fix/` | `fix/xxx` |
| 重构 | `refactor/` | `refactor/xxx` |
| 文档 | `docs/` | `docs/xxx` |
| 工程维护 | `chore/` | `chore/xxx` |

## 2. 进行更新/开发

### 2.1 检查工作区

```powershell
git status
git branch --show-current
```

如果存在未提交修改，先在所属任务分支提交，或使用暂存方式保存。不要把上一任务的修改直接带入新任务。

### 3.2 更新主分支，创建任务分支

以下以开发相机预览功能为例，后续命令中的分支名应保持一致：

```powershell
git switch master
git pull --ff-only origin master
git switch -c feature/camera-view
```

`--ff-only` 仅允许快进更新，避免拉取时意外产生合并提交。如果失败，停止后续步骤，检查本地 `master` 是否存在额外提交，不要用强制重置覆盖工作。

`git switch -c` 用于创建不存在的分支。如果同名分支已存在，确认它是否就是要继续的任务；新任务应使用新的分支名。

## 3. 提交

### 3.1 检查并提交

```powershell
git status
git diff
git diff --check
git add -p
git diff --cached
git commit -m "feat(camera): add camera preview"
```

- `git diff` 查看尚未暂存的已跟踪文件修改；新增文件应单独打开检查。
- `git diff --check` 检查空白错误等差异问题，不替代功能测试。
- `git add -p` 逐块暂存已跟踪文件的修改。新增文件需要使用 `git add 实际文件路径`。
- `git diff --cached` 查看本次将提交的内容。
- 只有确认全部修改都属于本任务时，才使用 `git add -A` 一次暂存全部变更。

每个提交尽量表达一个清晰改动。提交前确认没有误加入设备地址、敏感信息或本机临时文件。

## 4. 推送分支并创建 PR

### 4.1 首次推送

```powershell
git push -u origin feature/camera-view
```

`-u` 建立本地分支与远程分支的跟踪关系，之后在该分支上可直接使用 `git push`。

`commit` 只保存到本地；`push` 成功后，有仓库访问权限的人才能看到远程提交。未提交文件和被 `.gitignore` 忽略且未被跟踪的文件不会随推送上传。已经被 Git 跟踪的文件不会因为加入 `.gitignore` 就自动停止跟踪。

### 4.2 创建 PR

打开 [项目仓库](https://github.com/JaGuarPENG/KaanhPilot)，使用 **Compare & pull request**，或进入 **Pull requests → New pull request**。

确认分支方向：

- **base：`master`**，即目标分支。
- **compare：`feature/camera-view`**，即本次任务分支。

填写标题、说明并指定审查者。尚未完成的工作可以先创建 Draft PR。PR 描述建议包含：

```markdown
## 目的
解决什么问题，或实现什么功能。

## 主要改动
- 关键改动及影响范围。

## 验证结果
- 执行的测试命令和结果。
- 手工或设备验证步骤和结果。
- 尚未验证的部分及原因。

## 配置或兼容性影响
- 是否新增依赖、调整配置或需要额外迁移步骤；没有则写“无”。
```

### 4.3 处理审查反馈

继续在原任务分支修改、测试、提交，然后执行：

```powershell
git push
```

同一分支的新提交会自动进入已有 PR，不必重新创建 PR。

## 5. 开发期间同步其他人的更新

### 5.1 将最新 master 合并到自己的任务分支

先提交或暂存本地修改，再执行：

```powershell
git switch feature/camera-view
git fetch origin
git merge origin/master
```

如果没有冲突，Git 会完成合并或提示已经是最新状态。若有冲突：

1. 执行 `git status` 查看冲突文件。
2. 编辑文件，理解双方改动后保留正确内容，删除冲突标记。
3. 使用 `git add 实际文件路径` 标记各文件已解决。
4. 执行 `git commit` 完成合并。
5. 重新运行相关测试，确认通过后执行 `git push`。

如果需要取消尚未完成的合并，可以使用 `git merge --abort`。本流程使用 merge 同步主分支，不需要强制推送。

### 5.2 查看其他人的功能分支

保存自己的修改后，第一次获取某个远程分支：

```powershell
git fetch origin
git switch --track origin/feature/camera-view
```

如果本地已经有该分支：

```powershell
git switch feature/camera-view
git pull --ff-only
```

分支推送后即可查看，无需等待 PR 合并。远程 `master` 的变更不会自动出现在本地，必须主动获取更新。如果多人共同推送一个分支，应先沟通，再拉取和合并对方改动，不要强制覆盖。

## 6. 合并 PR 与清理分支

### 6.1 合并前确认

- 改动范围清晰，审查意见已处理。
- 最新提交的相关测试通过；已配置的 CI 检查通过。
- 冲突已经解决，配置和文档同步更新。
- 需要设备验证的变更已记录结果；未验证项已明确告知审查者。

建议团队统一使用 GitHub 的 **Squash and merge**，把一个任务整理为 `master` 上的一个提交。合并标题应准确描述最终改动。

合并后在 PR 页面点击 **Delete branch** 删除远程任务分支。如果仓库启用了合并后自动删除分支，不必重复删除。

### 6.2 清理本地分支

确认工作区干净后：

```powershell
git switch master
git pull --ff-only origin master
git fetch --prune
git branch -d feature/camera-view
```

`git fetch --prune` 清理已经不存在的远程分支引用，不会自动删除本地分支。

Squash 合并后，`git branch -d` 可能因为提交编号不同而拒绝删除。只有确认 **PR 已合并，且本地分支没有未纳入 PR 的工作** 后，才执行：

```powershell
git branch -D feature/camera-view
```

如果没有通过 GitHub 删除远程分支，确认 PR 已合并后也可以执行：

```powershell
git push origin --delete feature/camera-view
```

不要删除其他人仍在使用的分支。下个任务重新从更新后的 `master` 创建新分支。

## 7. 已经在旧分支修改时如何迁移

### 7.1 保留旧分支的全部提交和当前修改

如果当前就在旧分支，且希望完整保留其历史，直接创建新分支：

```powershell
git branch --show-current
git status
git switch -c feature/migrate-cxy
git diff
git add -A
git diff --cached
git commit -m "chore: migrate current project changes"
git push -u origin feature/migrate-cxy
```
如果工作区没有改动，跳过 `git add` 和 `git commit`。

### 7.2 只把未提交修改移到最新 master 创建的分支

```powershell
git stash push -u -m "move pending changes to new feature branch"
git stash list
git switch master
git pull --ff-only origin master
git switch -c feature/new-task
git stash apply 'stash@{0}'
```

仅在第一条命令确实创建了新的 stash 后，才使用这里的 `stash@{0}`；否则它可能指向更早的暂存记录。`-u` 包含未跟踪文件，但不包含被忽略的文件。

恢复时如有冲突，需要手工解决。这些命令只迁移未提交修改，不迁移 `cxy` 已有的提交。已有提交如需单独迁移，应先查看 `git log origin/master..cxy --oneline`，确认所需提交与依赖关系，再按顺序使用 `git cherry-pick 提交编号`。

确认修改完整恢复，并在新分支提交保存后，检查 `git stash list`，删除此次迁移对应的暂存记录。若它仍是最新一条：

```powershell
git stash drop 'stash@{0}'
```

## 8. 日常流程速查

1. `git status`：确认并保存当前工作。
2. `git switch master`：回到主分支。
3. `git pull --ff-only origin master`：更新主分支。
4. `git switch -c feature/任务名称`：创建新任务分支。
5. 开发、测试、检查差异，并提交相关修改。
6. `git push -u origin feature/任务名称`：首次推送任务分支。
7. 创建目标为 `master` 的 PR，处理审查反馈。
8. 如主分支有更新，合并 `origin/master`，重新测试并推送。
9. PR 审查和检查通过后合并，删除远程任务分支。
10. 更新本地 `master`，清理已合并的本地分支，开始下一任务。