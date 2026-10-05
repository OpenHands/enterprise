/* eslint-disable i18next/no-literal-string */
const ZH_CN: Record<string, string> = {
  Language: "语言",
  "Mock only": "仅模拟",
  "Pages and modals": "页面与对话框",
  "Cards are batched by sprint and shown in flow order. View opens that exact screen. Needs review marks access, provisioning, org status, membership, first install, and the Super Admin dashboard.":
    "卡片按迭代分组，并按流程顺序排列。查看会打开对应的那个画面。需要评审标出访问权限、开通用户、组织状态、成员关系、首次安装，以及超级管理员仪表盘。",
  "Needs review": "需要评审",
  View: "查看",
  "Review.": "评审。",
  "OHE-3384 · PRD OHE-651": "OHE-3384 · 产品需求 OHE-651",
  "First-time Super Admin onboarding": "超级管理员首次上手",
  "Install wizard, in order: welcome, company, first organization, then org LLM and the starter modal. The acceptance criteria also say the organization is created in the background with the Super Admin as owner. This wizard still asks them to name it. Unit tests, the feature flag, the OpenHands-Cloud E2E, and the bug bash are not screens on this page.":
    "安装向导按顺序是：欢迎、公司、第一个组织，然后是组织语言模型和起步对话框。验收标准还要求在后台创建组织，并把超级管理员设为所有者。这个向导仍会让他们给组织起名。单元测试、功能开关、OpenHands Cloud 端到端测试和缺陷清扫都不是本页上的画面。",
  Welcome: "欢迎",
  "Opens the Super Admin install. Progress is stored in this browser only.":
    "打开超级管理员安装。进度只存在这个浏览器里。",
  "Five-step install bar from welcome through first automation":
    "从欢迎到第一次自动化的五步安装条",
  "Animated OpenHands mark and welcome copy":
    "带动画的 OpenHands 标志和欢迎文案",
  "Next marks this step done in this browser":
    "下一步会在这个浏览器里把本步标成完成",
  "Confirm Next only writes the oh-sa-nux welcome flag in this browser. A finished install must not return here unless that storage is cleared.":
    "确认下一步只在这个浏览器里写入 oh-sa-nux 的欢迎标记。安装完成后不应再回到这里，除非清掉那份存储。",
  "First-install NUX": "首次安装引导",
  Company: "公司",
  "Company name and logo, saved to the server.":
    "公司名称和标志，会保存到服务器。",
  "Company name and logo": "公司名称和标志",
  "The name and logo are saved to the server": "名称和标志会保存到服务器",
  "Confirm the name and logo are saved and the logo shows for other users.":
    "确认名称和标志已保存，并且其他用户也能看到标志。",
  Organization: "组织",
  "Names the first organization, then continues to LLM settings.":
    "为第一个组织起名，然后进入语言模型设置。",
  "Names the first organization on the instance": "为实例上的第一个组织起名",
  "Continues to LLM settings": "继续进入语言模型设置",
  "Leaves the starter modal pending": "起步对话框保持待打开",
  "This should run only after welcome and company are done, then open LLM settings with the starter modal pending. It must not skip to the dashboard.":
    "这一步只应在欢迎和公司都完成后出现，然后打开语言模型设置，并让起步对话框待打开。不能直接跳到仪表盘。",
  "Language Model": "语言模型",
  "Org LLM defaults. After first install, the starter modal opens on this page.":
    "组织的语言模型默认值。首次安装之后，起步对话框会在这个页面打开。",
  "Last step of first install opens the starter modal here":
    "首次安装的最后一步会在这里打开起步对话框",
  "LLM profile rows no longer clip on small screens":
    "小屏幕上语言模型配置行不再被裁切",
  "The starter modal should open only while starterModalPending is set, and closing it must clear that flag. Check the profile rows at a narrow width.":
    "起步对话框只应在 starterModalPending 被设置时打开，关掉它必须清掉这个标记。把宽度缩窄，检查配置行。",
  "Starter setup": "起步设置",
  "After install, add an LLM and an integration. Add an LLM starts open. Skip closes the modal.":
    "安装之后，添加语言模型和集成。添加语言模型这一项默认展开。跳过会关掉对话框。",
  "Add an LLM starts open": "添加语言模型默认展开",
  "The pinned step stays open while another step is hovered":
    "悬停另一步时，钉住的步骤保持展开",
  "Providers and models come from the server; pick one model":
    "提供方和模型来自服务器，只选一个模型",
  "Skip dismisses the modal": "跳过会关掉对话框",
  "Skip must clear starterModalPending. Hovering another step must not collapse the pinned one. The catalog forceOpen path must not clear a real user's nux.":
    "跳过必须清掉 starterModalPending。悬停另一步不能收起钉住的步骤。目录的 forceOpen 路径不能清掉真实用户的引导状态。",
  "Onboarding guide to the aha moment": "引导到第一次成功",
  "After install, the setup guide follows the Super Admin across the product: organization, LLM, integration, automation, invite, then optional SAML. It is for the first organization they own. A new admin of an organization that is already configured should not see it. There is no automation-template screen here; the guide links to /automations. Guide progress is local storage, not a server flag, so it does not yet know that an org is fully configured.":
    "安装之后，设置指南会跟着超级管理员走完产品：组织、语言模型、集成、自动化、邀请，然后是可选的 SAML。它面向他们拥有的第一个组织。已经配置好的组织里，新来的管理员不应看到它。这里没有自动化模板画面；指南链接到 /automations。指南进度在本地存储里，不是服务器标记，所以它还不知道某个组织是否已经完全配置好。",
  "Setup guide": "设置指南",
  "Checklist after install. Dismissing it hides the guide.":
    "安装之后的清单。关闭它会把指南藏起来。",
  "Steps for org, LLM, integration, automation, invite, and optional SAML":
    "组织、语言模型、集成、自动化、邀请，以及可选 SAML 的步骤",
  "The active step stays open while another step is previewed":
    "预览另一步时，当前步骤保持展开",
  "The guide can be dismissed": "指南可以关闭",
  "Confirm a hovered step does not replace the pinned step. Dismiss should hide the guide without clearing oh-sa-nux, and Start should bring it back.":
    "确认悬停的步骤不会替换钉住的步骤。关闭应藏起指南，但不清掉 oh-sa-nux，开始应把它带回来。",
  "Remove setup guide": "移除设置指南",
  "Confirms hiding the setup guide.": "确认藏起设置指南。",
  "Confirms hiding the setup guide": "确认藏起设置指南",
  "The guide can be started again": "指南可以重新开始",
  "Dismiss should only flip the local setup-guide flag. It must not clear oh-sa-nux or org data.":
    "关闭只应翻转本地的设置指南标记。不能清掉 oh-sa-nux 或组织数据。",
  Integrations: "集成",
  "Git and project resolvers. Hub addresses redirect here. The Integrations Hub is not on this branch.":
    "Git 和项目解析器。中心地址会重定向到这里。集成中心不在这个分支上。",
  "Personal Integrations is the resolver list": "个人集成就是解析器列表",
  "GitHub, GitLab, Jira, Linear, Slack, and the other git providers":
    "GitHub、GitLab、Jira、Linear、Slack，以及其他 Git 提供方",
  "The Integrations Hub is removed; old hub addresses redirect here":
    "集成中心已移除；旧的中心地址会重定向到这里",
  "Confirm hub routes redirect here and ENABLE_INTEGRATIONS_HUB is not on this branch. Resolver connect should still work for each provider.":
    "确认中心路由会重定向到这里，并且这个分支上没有 ENABLE_INTEGRATIONS_HUB。每个提供方的解析器连接仍应可用。",
  "Configure integration": "配置集成",
  "Connect a provider with a token or webhook.":
    "用令牌或 webhook 连接一个提供方。",
  "Token or webhook form for the selected provider":
    "所选提供方的令牌或 webhook 表单",
  "The Integrations Hub connect flow is not on this branch":
    "集成中心的连接流程不在这个分支上",
  "This is the legacy resolver form. Confirm the token or webhook is not shown again after save, and that no hub connector is mounted.":
    "这是旧的解析器表单。确认保存后不再显示令牌或 webhook，并且没有挂上中心连接器。",
  MCP: "MCP",
  "Model Context Protocol servers. The label is a display name; the saved config key stays as entered.":
    "模型上下文协议服务器。标签是显示名称；保存的配置键保持输入时的样子。",
  "Servers render as rows, like Integrations": "服务器排成行，和集成一样",
  "Display names capitalize and drop hyphens; the saved config key is unchanged":
    "显示名称会首字母大写并去掉连字符；保存的配置键不变",
  "Known servers use Integrations marks and Simple Icons":
    "已知服务器使用集成标志和 Simple Icons",
  "Confirm the display name is not written back as the config key. A short name can match the wrong logo because matching is a substring.":
    "确认显示名称不会写回配置键。短名称可能配上错误标志，因为匹配用的是子串。",
  "Add MCP server": "添加 MCP 服务器",
  "Add or edit a server. The saved name is the config key.":
    "添加或编辑服务器。保存的名称就是配置键。",
  "One dialog adds a server and edits an existing one":
    "同一个对话框既能添加服务器，也能编辑已有的",
  "The saved name is the config key": "保存的名称就是配置键",
  "Add and edit share this dialog. Confirm the saved key is the name field, not the capitalized display label.":
    "添加和编辑共用这个对话框。确认保存的键是名称字段，不是首字母大写后的显示标签。",
  "Delete MCP server": "删除 MCP 服务器",
  "Confirms removing a server.": "确认移除一台服务器。",
  "Confirms removal of the server": "确认移除这台服务器",
  "The config key is what gets deleted": "删掉的是配置键",
  "Confirm deletion removes the stored config key, even when the row shows a display name.":
    "确认删除会移除存下的配置键，即使这一行显示的是显示名称。",
  Members: "成员",
  "Email and role for people in this organization. First and last name are not stored.":
    "这个组织里每个人的邮箱和角色。不存储名和姓。",
  "Invite, role change, and removal stay on email and role":
    "邀请、改角色和移除都只涉及邮箱和角色",
  "First and last name are not stored, so they are not shown":
    "不存储名和姓，所以也不显示",
  "Setup guide invite step links here": "设置指南的邀请步骤链接到这里",
  "Do not expect a first or last name. Invite, role change, and remove still use email and role. Removal must keep the last-owner rule.":
    "不要期待名或姓。邀请、改角色和移除仍使用邮箱和角色。移除必须守住最后一位所有者的规则。",
  "Invite members": "邀请成员",
  "Invite people by email and role into the current organization.":
    "用邮箱和角色把人邀请进当前组织。",
  "Invite by email and role": "按邮箱和角色邀请",
  "Restyled with the neo modal chrome": "对话框外观已换成 neo 样式",
  "Existing membership action. Confirm the neo dialog still submits email and role, and does not ask for a name.":
    "这是原有的成员操作。确认 neo 对话框仍提交邮箱和角色，并且不询问姓名。",
  "Change member role": "更改成员角色",
  "Confirms the new role for someone in the current organization.":
    "确认为当前组织里的某个人设置的新角色。",
  "Confirms the new role": "确认新角色",
  "Existing confirm. The submitted role should be the one shown, including owner, admin, and member.":
    "这是原有的确认框。提交的角色应是画面上显示的那个，包括所有者、管理员和成员。",
  "Remove member": "移除成员",
  "Confirms removing someone from the current organization.":
    "确认把某人移出当前组织。",
  "Confirms removal from this organization": "确认从本组织移除",
  "Existing confirm. Removal must still refuse to drop the last owner.":
    "这是原有的确认框。移除仍必须拒绝去掉最后一位所有者。",
  "OHE-3433 · PRD OHE-651": "OHE-3433 · 产品需求 OHE-651",
  "Super Admin dashboard": "超级管理员仪表盘",
  "Opened from the org menu in settings when the viewer is a Super Admin. Then create organizations, grant and revoke Super Admins, provision and manage users, and edit org membership. Delete and disable live on Manage user. SSO is not configurable here, and Instance Settings has no documentation link for the SSO environment variable. Email and auto-org on that page are read-only. The gate is ENABLE_SUPER_ADMIN, accepting true or 1. The OpenHands-Cloud E2E and the bug bash are still outside this UI.":
    "查看者是超级管理员时，从设置里的组织菜单打开。然后可以创建组织、授予和撤销超级管理员、开通并管理用户，以及编辑组织成员关系。删除和停用在管理用户里。这里不能配置单点登录，实例设置也没有单点登录环境变量的文档链接。那一页上的邮件和自动建组织是只读的。开关是 ENABLE_SUPER_ADMIN，接受 true 或 1。OpenHands Cloud 端到端测试和缺陷清扫仍在这个界面之外。",
  Dashboard: "仪表盘",
  "Instance overview. A normal org member must not open this route.":
    "实例总览。普通组织成员不能打开这个路由。",
  "Gated by ENABLE_SUPER_ADMIN, accepting true or 1":
    "由 ENABLE_SUPER_ADMIN 把关，接受 true 或 1",
  "Instance overview with the next setup-guide step":
    "实例总览，并带出设置指南的下一步",
  "Conversation list and stop actions": "对话列表和停止操作",
  "Org menu pins Super Admin with a yellow shield":
    "组织菜单用黄色盾牌钉住超级管理员",
  "Confirm ENABLE_SUPER_ADMIN accepts both true and 1, and a normal org member is denied. Review empty, error, and stop-conversation states more than layout. The shield belongs only on /super-admin.":
    "确认 ENABLE_SUPER_ADMIN 同时接受 true 和 1，并且普通组织成员会被拒绝。空状态、错误状态和停止对话比版面更值得看。盾牌只应出现在 /super-admin。",
  Organizations: "组织",
  "List, create, open, suspend, and resume organizations. Existing orgs default to active.":
    "列出、创建、打开、暂停和恢复组织。已有组织默认是活跃的。",
  "Create, open, suspend, and resume organizations":
    "创建、打开、暂停和恢复组织",
  "Suspend blocks usage and can be reversed": "暂停会挡住使用，并且可以恢复",
  "Opening an org you are not in asks you to join first":
    "打开一个你不在其中的组织时，会先要求你加入",
  "Existing organizations default to active": "已有组织默认是活跃的",
  "Review migration 144. Existing orgs must default to active. Suspend has to block usage, resume has to undo it, and callers must not ignore org.status.":
    "看迁移 144。已有组织必须默认活跃。暂停必须挡住使用，恢复必须撤销暂停，调用方不能忽略 org.status。",
  "Org status": "组织状态",
  "Create organization": "创建组织",
  "Name, owner, and contact email for a new organization.":
    "新组织的名称、所有者和联系邮箱。",
  "Opened from the org menu and from Super Admin organizations":
    "从组织菜单和超级管理员的组织页打开",
  "Sets the name, owner, and contact email": "设置名称、所有者和联系邮箱",
  "Confirm the owner and contact email are the values that get stored, and the org is created active.":
    "确认存下来的是所有者和联系邮箱，并且组织创建后是活跃的。",
  Membership: "成员关系",
  Users: "用户",
  "Everyone on the instance. Provision a user and edit membership across organizations.":
    "实例上的所有人。开通用户，并跨组织编辑成员关系。",
  "Provision a user into one or more organizations":
    "把用户开通到一个或多个组织",
  "Role is chosen per organization": "每个组织单独选择角色",
  "Manage that person's access across organizations":
    "管理这个人在各个组织里的访问权限",
  "Password and API key are shown after provision":
    "开通之后会显示密码和 API 密钥",
  "Confirm only a Super Admin can provision, the role is stored per org, and the returned API key is not logged. Cross-org writes go through the membership service.":
    "确认只有超级管理员可以开通，角色按组织存储，返回的 API 密钥不会被记入日志。跨组织写入要走成员关系服务。",
  Provisioning: "开通用户",
  "Provision User": "开通用户",
  "Creates a user in one or more organizations and can return a password and API key.":
    "在一个或多个组织里创建用户，并可以返回密码和 API 密钥。",
  "Create a user in one or more organizations": "在一个或多个组织里创建用户",
  "Can return a password and an API key": "可以返回密码和 API 密钥",
  "Confirm who can call provision, that each selected org stores its own role, and that a failed call does not show credentials.":
    "确认谁可以调用开通，每个选中的组织各自存角色，失败的调用不会显示凭据。",
  "Provision credentials": "开通凭据",
  "Shows the new password and LiteLLM API key in plain text. Confirm those values are not logged.":
    "用明文显示新密码和 LiteLLM API 密钥。确认这些值不会被记入日志。",
  "Shows the new password in plain text": "用明文显示新密码",
  "Shows the LiteLLM API key in plain text": "用明文显示 LiteLLM API 密钥",
  "Copy to clipboard": "复制到剪贴板",
  "The LiteLLM key is plain text. Confirm it is not logged or written to localStorage, and that closing the dialog removes it from the page.":
    "LiteLLM 密钥是明文。确认它不会被记入日志或写入 localStorage，并且关掉对话框后页面上不再有它。",
  "Manage user": "管理用户",
  "Membership and role for one person across organizations. Watch the last-owner rule.":
    "一个人在各个组织里的成员关系和角色。注意最后一位所有者的规则。",
  "Org access and role for one person": "一个人的组织访问权限和角色",
  "Add or change membership in specific organizations":
    "在指定组织里添加或更改成员关系",
  "Last-owner rule still applies": "最后一位所有者的规则仍然适用",
  "Review cross-org add, remove, and role changes. A Super Admin must not remove the last owner of an org.":
    "看跨组织的添加、移除和角色变更。超级管理员不能移除某个组织的最后一位所有者。",
  "Grant yourself access": "给自己授权",
  "A Super Admin picks a role and joins an organization before opening it.":
    "超级管理员先选一个角色并加入组织，然后才能打开它。",
  "A Super Admin must join an organization before opening it":
    "超级管理员必须先加入组织，才能打开它",
  "The role is chosen at join time": "加入时选择角色",
  "Opening an org without membership must stay blocked until this completes, and the role written must be the one selected.":
    "没有成员关系时打开组织必须一直被拦住，直到这一步完成，并且写入的角色必须是选中的那个。",
  "Super Admins": "超级管理员",
  "Who holds the instance role. Grant and revoke require manage_super_admins.":
    "谁拥有实例角色。授予和撤销需要 manage_super_admins。",
  "Lists who holds the instance Super Admin role":
    "列出谁拥有实例超级管理员角色",
  "Grant the role by email": "按邮箱授予该角色",
  "Revoke requires manage_super_admins": "撤销需要 manage_super_admins",
  "A normal org member must not reach this page":
    "普通组织成员不能进入这个页面",
  "Grant and revoke must require manage_super_admins. Confirm this route cannot bypass the Super Admin access check, and an org admin cannot open it.":
    "授予和撤销必须要求 manage_super_admins。确认这个路由不能绕过超级管理员访问检查，组织管理员也不能打开它。",
  Access: "访问权限",
  "Grant Super Admin": "授予超级管理员",
  "Gives the instance Super Admin role to an email. A normal org member must not be able to do this.":
    "把实例超级管理员角色授给一个邮箱。普通组织成员不能做这件事。",
  "Grants the instance Super Admin role by email":
    "按邮箱授予实例超级管理员角色",
  "Requires manage_super_admins": "需要 manage_super_admins",
  "A caller without manage_super_admins must be rejected. Confirm granting does not also skip org membership checks for that user.":
    "没有 manage_super_admins 的调用方必须被拒绝。确认授予角色时，不会同时跳过该用户的组织成员检查。",
  "Instance Settings": "实例设置",
  "Instance logo and email status. Check the permission-denied state, not the layout.":
    "实例标志和邮件状态。看的是无权限状态，不是版面。",
  "Instance logo": "实例标志",
  "Email delivery status": "邮件送达状态",
  "Optional SAML setup step links here": "可选的 SAML 设置步骤链接到这里",
  "Review the permission-denied and error states. Logo and email status must stay behind Super Admin access.":
    "看无权限和错误状态。标志和邮件状态必须留在超级管理员访问权限后面。",
  "Settings chrome · this branch": "设置外观 · 当前分支",
  "Neo theme on the screens these flows land in":
    "这些流程落到的画面上的 neo 主题",
  "Not its own product ticket. Visual pass on the settings screens around the two sprints. Review layout and save behavior, not access or provisioning.":
    "这不是单独的产品工单。对这两次迭代周围的设置画面做外观检查。看版面和保存行为，不看访问权限或开通用户。",
  "Usage for the current organization.": "当前组织的用量。",
  "Usage dashboard restyled with neo theme tokens":
    "用量仪表盘已换成 neo 主题 token",
  "Loading skeletons match the neo chrome": "加载骨架和 neo 外观一致",
  "Visual regression on the usage widgets and skeletons. No access or billing logic changed on this page.":
    "看用量小部件和骨架有没有外观回退。这个页面没有改访问权限或账单逻辑。",
  Budgets: "预算",
  "Spending limits and alerts for the organization.": "组织的支出上限和提醒。",
  "Budgets restyled with neo theme tokens": "预算已换成 neo 主题 token",
  "Visual regression on budget tabs and alerts. Confirm limits still save the same way.":
    "看预算标签和提醒有没有外观回退。确认上限仍按原来的方式保存。",
  Condenser: "Condenser",
  "How conversation history is condensed for the organization.":
    "组织如何压缩对话历史。",
  "Grouped under Language Model in the reordered settings nav":
    "在重新排序的设置导航里，归在语言模型下面",
  "Visual and nav-order check. Confirm condenser settings still save, and the ACP disable still applies if it did before.":
    "看外观和导航顺序。确认 condenser 设置仍能保存，并且如果以前会禁用 ACP，现在仍然会。",
  Verification: "验证",
  "Checks that the organization LLM setup works.":
    "检查组织的语言模型设置是否可用。",
  "Visual and nav-order check. Confirm verification still runs against the org LLM setup.":
    "看外观和导航顺序。确认验证仍针对组织的语言模型设置运行。",
  "Billing & Credits": "账单与积分",
  "Plan, credits, and payment for the organization.":
    "组织的方案、积分和付款。",
  "Billing restyled with neo theme tokens": "账单已换成 neo 主题 token",
  "Visual pass. This is not the provisioned LiteLLM key. Confirm plan and credit actions still submit.":
    "做一遍外观检查。这不是开通时返回的 LiteLLM 密钥。确认方案和积分操作仍会提交。",
  "Name and deletion for the current organization.": "当前组织的名称和删除。",
  "Create organization is available from the org menu":
    "可以从组织菜单创建组织",
  "Rename and delete stay on this organization": "重命名和删除仍作用于这个组织",
  "Confirm Create organization in the org menu does not break org switching. Delete here is the current org, not Super Admin suspend.":
    "确认组织菜单里的创建组织不会弄坏组织切换。这里的删除是当前组织，不是超级管理员的暂停。",
  "Change organization name": "更改组织名称",
  "Renames the current organization.": "重命名当前组织。",
  "Renames the current organization": "重命名当前组织",
  "Existing rename. Confirm the saved name is the field value and the org menu updates after save.":
    "这是原有的重命名。确认保存的名称就是字段里的值，并且保存后组织菜单会更新。",
  "Delete organization": "删除组织",
  "Confirms deleting the current organization.": "确认删除当前组织。",
  "Confirms deletion of the current organization": "确认删除当前组织",
  "Existing delete. This is not Super Admin suspend. Confirm it deletes only the current org and still requires the confirm step.":
    "这是原有的删除。这不是超级管理员的暂停。确认它只删除当前组织，并且仍需要确认这一步。",
  Agent: "代理",
  "Agent behavior settings for the organization.": "组织的代理行为设置。",
  "Visual pass on the neo chrome. Confirm agent settings still save with the existing form.":
    "对 neo 外观做一遍检查。确认代理设置仍用现有表单保存。",
  "API Keys": "API 密钥",
  "Keys the organization uses to call OpenHands.":
    "组织用来调用 OpenHands 的密钥。",
  "Visual pass. Keys should still save immediately. This is separate from the LiteLLM key returned by provisioning.":
    "做一遍外观检查。密钥仍应立即保存。这和开通时返回的 LiteLLM 密钥是分开的。",
  Secrets: "机密",
  "Named secrets available to conversations.": "对话可以使用的具名机密。",
  "Visual pass. Adding and deleting a secret should still hit the secrets API immediately, with values masked after save.":
    "做一遍外观检查。添加和删除机密仍应立刻打到机密接口，保存后值要被遮住。",
  Skills: "技能",
  "Organization skills the agent can load.": "代理可以加载的组织技能。",
  "Visual pass. Confirm adding and removing a skill still saves immediately.":
    "做一遍外观检查。确认添加和移除技能仍会立即保存。",
  "OpenHands-Neo settings chrome, form controls, and toasts":
    "OpenHands-Neo 的设置外观、表单控件和提示",
  "Settings nav brand reads Account": "设置导航的品牌文案是账户",
  Usage: "用量",
};

const ZH_TW: Record<string, string> = {
  Language: "語言",
  "Mock only": "僅模擬",
  "Pages and modals": "頁面與對話框",
  "Cards are batched by sprint and shown in flow order. View opens that exact screen. Needs review marks access, provisioning, org status, membership, first install, and the Super Admin dashboard.":
    "卡片按迭代分組，並按流程順序排列。檢視會開啟對應的那個畫面。需要評審標出存取權限、開通使用者、組織狀態、成員關係、首次安裝，以及超級管理員儀表板。",
  "Needs review": "需要評審",
  View: "檢視",
  "Review.": "評審。",
  "OHE-3384 · PRD OHE-651": "OHE-3384 · 產品需求 OHE-651",
  "First-time Super Admin onboarding": "超級管理員首次上手",
  "Install wizard, in order: welcome, company, first organization, then org LLM and the starter modal. The acceptance criteria also say the organization is created in the background with the Super Admin as owner. This wizard still asks them to name it. Unit tests, the feature flag, the OpenHands-Cloud E2E, and the bug bash are not screens on this page.":
    "安裝嚮導按順序是：歡迎、公司、第一個組織，然後是組織語言模型和起步對話框。驗收標準還要求在後臺建立組織，並把超級管理員設為所有者。這個嚮導仍會讓他們給組織起名。單元測試、功能開關、OpenHands Cloud 端到端測試和缺陷清掃都不是本頁上的畫面。",
  Welcome: "歡迎",
  "Opens the Super Admin install. Progress is stored in this browser only.":
    "開啟超級管理員安裝。進度只存在這個瀏覽器裡。",
  "Five-step install bar from welcome through first automation":
    "從歡迎到第一次自動化的五步安裝條",
  "Animated OpenHands mark and welcome copy":
    "帶動畫的 OpenHands 標誌和歡迎文案",
  "Next marks this step done in this browser":
    "下一步會在這個瀏覽器裡把本步標成完成",
  "Confirm Next only writes the oh-sa-nux welcome flag in this browser. A finished install must not return here unless that storage is cleared.":
    "確認下一步只在這個瀏覽器裡寫入 oh-sa-nux 的歡迎標記。安裝完成後不應再回到這裡，除非清掉那份儲存。",
  "First-install NUX": "首次安裝引導",
  Company: "公司",
  "Company name and logo, saved to the server.":
    "公司名稱和標誌，會儲存到伺服器。",
  "Company name and logo": "公司名稱和標誌",
  "The name and logo are saved to the server": "名稱和標誌會儲存到伺服器",
  "Confirm the name and logo are saved and the logo shows for other users.":
    "確認名稱和標誌已儲存，並且其他使用者也能看到標誌。",
  Organization: "組織",
  "Names the first organization, then continues to LLM settings.":
    "為第一個組織起名，然後進入語言模型設定。",
  "Names the first organization on the instance": "為例項上的第一個組織起名",
  "Continues to LLM settings": "繼續進入語言模型設定",
  "Leaves the starter modal pending": "起步對話框保持待開啟",
  "This should run only after welcome and company are done, then open LLM settings with the starter modal pending. It must not skip to the dashboard.":
    "這一步只應在歡迎和公司都完成後出現，然後開啟語言模型設定，並讓起步對話框待開啟。不能直接跳到儀表板。",
  "Language Model": "語言模型",
  "Org LLM defaults. After first install, the starter modal opens on this page.":
    "組織的語言模型預設值。首次安裝之後，起步對話框會在這個頁面開啟。",
  "Last step of first install opens the starter modal here":
    "首次安裝的最後一步會在這裡開啟起步對話框",
  "LLM profile rows no longer clip on small screens":
    "小螢幕上語言模型配置行不再被裁切",
  "The starter modal should open only while starterModalPending is set, and closing it must clear that flag. Check the profile rows at a narrow width.":
    "起步對話框只應在 starterModalPending 被設定時開啟，關掉它必須清掉這個標記。把寬度縮窄，檢查配置行。",
  "Starter setup": "起步設定",
  "After install, add an LLM and an integration. Add an LLM starts open. Skip closes the modal.":
    "安裝之後，新增語言模型和整合。新增語言模型這一項預設展開。跳過會關掉對話框。",
  "Add an LLM starts open": "新增語言模型預設展開",
  "The pinned step stays open while another step is hovered":
    "懸停另一步時，釘住的步驟保持展開",
  "Providers and models come from the server; pick one model":
    "提供方和模型來自伺服器，只選一個模型",
  "Skip dismisses the modal": "跳過會關掉對話框",
  "Skip must clear starterModalPending. Hovering another step must not collapse the pinned one. The catalog forceOpen path must not clear a real user's nux.":
    "跳過必須清掉 starterModalPending。懸停另一步不能收起釘住的步驟。目錄的 forceOpen 路徑不能清掉真實使用者的引導狀態。",
  "Onboarding guide to the aha moment": "引導到第一次成功",
  "After install, the setup guide follows the Super Admin across the product: organization, LLM, integration, automation, invite, then optional SAML. It is for the first organization they own. A new admin of an organization that is already configured should not see it. There is no automation-template screen here; the guide links to /automations. Guide progress is local storage, not a server flag, so it does not yet know that an org is fully configured.":
    "安裝之後，設定指南會跟著超級管理員走完產品：組織、語言模型、整合、自動化、邀請，然後是可選的 SAML。它面向他們擁有的第一個組織。已經配置好的組織里，新來的管理員不應看到它。這裡沒有自動化模板畫面；指南連結到 /automations。指南進度在本地儲存裡，不是伺服器標記，所以它還不知道某個組織是否已經完全配置好。",
  "Setup guide": "設定指南",
  "Checklist after install. Dismissing it hides the guide.":
    "安裝之後的清單。關閉它會把指南藏起來。",
  "Steps for org, LLM, integration, automation, invite, and optional SAML":
    "組織、語言模型、整合、自動化、邀請，以及可選 SAML 的步驟",
  "The active step stays open while another step is previewed":
    "預覽另一步時，當前步驟保持展開",
  "The guide can be dismissed": "指南可以關閉",
  "Confirm a hovered step does not replace the pinned step. Dismiss should hide the guide without clearing oh-sa-nux, and Start should bring it back.":
    "確認懸停的步驟不會替換釘住的步驟。關閉應藏起指南，但不清掉 oh-sa-nux，開始應把它帶回來。",
  "Remove setup guide": "移除設定指南",
  "Confirms hiding the setup guide.": "確認藏起設定指南。",
  "Confirms hiding the setup guide": "確認藏起設定指南",
  "The guide can be started again": "指南可以重新開始",
  "Dismiss should only flip the local setup-guide flag. It must not clear oh-sa-nux or org data.":
    "關閉只應翻轉本地的設定指南標記。不能清掉 oh-sa-nux 或組織資料。",
  Integrations: "整合",
  "Git and project resolvers. Hub addresses redirect here. The Integrations Hub is not on this branch.":
    "Git 和專案解析器。中心地址會重定向到這裡。整合中心不在這個分支上。",
  "Personal Integrations is the resolver list": "個人整合就是解析器列表",
  "GitHub, GitLab, Jira, Linear, Slack, and the other git providers":
    "GitHub、GitLab、Jira、Linear、Slack，以及其他 Git 提供方",
  "The Integrations Hub is removed; old hub addresses redirect here":
    "整合中心已移除；舊的中心地址會重定向到這裡",
  "Confirm hub routes redirect here and ENABLE_INTEGRATIONS_HUB is not on this branch. Resolver connect should still work for each provider.":
    "確認中心路由會重定向到這裡，並且這個分支上沒有 ENABLE_INTEGRATIONS_HUB。每個提供方的解析器連線仍應可用。",
  "Configure integration": "配置整合",
  "Connect a provider with a token or webhook.":
    "用令牌或 webhook 連線一個提供方。",
  "Token or webhook form for the selected provider":
    "所選提供方的令牌或 webhook 表單",
  "The Integrations Hub connect flow is not on this branch":
    "整合中心的連線流程不在這個分支上",
  "This is the legacy resolver form. Confirm the token or webhook is not shown again after save, and that no hub connector is mounted.":
    "這是舊的解析器表單。確認儲存後不再顯示令牌或 webhook，並且沒有掛上中心聯結器。",
  MCP: "MCP",
  "Model Context Protocol servers. The label is a display name; the saved config key stays as entered.":
    "模型上下文協議伺服器。標籤是顯示名稱；儲存的配置鍵保持輸入時的樣子。",
  "Servers render as rows, like Integrations": "伺服器排成行，和整合一樣",
  "Display names capitalize and drop hyphens; the saved config key is unchanged":
    "顯示名稱會首字母大寫並去掉連字元；儲存的配置鍵不變",
  "Known servers use Integrations marks and Simple Icons":
    "已知伺服器使用整合標誌和 Simple Icons",
  "Confirm the display name is not written back as the config key. A short name can match the wrong logo because matching is a substring.":
    "確認顯示名稱不會寫回配置鍵。短名稱可能配上錯誤標誌，因為匹配用的是子串。",
  "Add MCP server": "新增 MCP 伺服器",
  "Add or edit a server. The saved name is the config key.":
    "新增或編輯伺服器。儲存的名稱就是配置鍵。",
  "One dialog adds a server and edits an existing one":
    "同一個對話框既能新增伺服器，也能編輯已有的",
  "The saved name is the config key": "儲存的名稱就是配置鍵",
  "Add and edit share this dialog. Confirm the saved key is the name field, not the capitalized display label.":
    "新增和編輯共用這個對話框。確認儲存的鍵是名稱欄位，不是首字母大寫後的顯示標籤。",
  "Delete MCP server": "刪除 MCP 伺服器",
  "Confirms removing a server.": "確認移除一臺伺服器。",
  "Confirms removal of the server": "確認移除這臺伺服器",
  "The config key is what gets deleted": "刪掉的是配置鍵",
  "Confirm deletion removes the stored config key, even when the row shows a display name.":
    "確認刪除會移除存下的配置鍵，即使這一行顯示的是顯示名稱。",
  Members: "成員",
  "Email and role for people in this organization. First and last name are not stored.":
    "這個組織里每個人的郵箱和角色。不儲存名和姓。",
  "Invite, role change, and removal stay on email and role":
    "邀請、改角色和移除都只涉及郵箱和角色",
  "First and last name are not stored, so they are not shown":
    "不儲存名和姓，所以也不顯示",
  "Setup guide invite step links here": "設定指南的邀請步驟連結到這裡",
  "Do not expect a first or last name. Invite, role change, and remove still use email and role. Removal must keep the last-owner rule.":
    "不要期待名或姓。邀請、改角色和移除仍使用郵箱和角色。移除必須守住最後一位所有者的規則。",
  "Invite members": "邀請成員",
  "Invite people by email and role into the current organization.":
    "用郵箱和角色把人邀請進當前組織。",
  "Invite by email and role": "按郵箱和角色邀請",
  "Restyled with the neo modal chrome": "對話框外觀已換成 neo 樣式",
  "Existing membership action. Confirm the neo dialog still submits email and role, and does not ask for a name.":
    "這是原有的成員操作。確認 neo 對話框仍提交郵箱和角色，並且不詢問姓名。",
  "Change member role": "更改成員角色",
  "Confirms the new role for someone in the current organization.":
    "確認為當前組織里的某個人設定的新角色。",
  "Confirms the new role": "確認新角色",
  "Existing confirm. The submitted role should be the one shown, including owner, admin, and member.":
    "這是原有的確認框。提交的角色應是畫面上顯示的那個，包括所有者、管理員和成員。",
  "Remove member": "移除成員",
  "Confirms removing someone from the current organization.":
    "確認把某人移出當前組織。",
  "Confirms removal from this organization": "確認從本組織移除",
  "Existing confirm. Removal must still refuse to drop the last owner.":
    "這是原有的確認框。移除仍必須拒絕去掉最後一位所有者。",
  "OHE-3433 · PRD OHE-651": "OHE-3433 · 產品需求 OHE-651",
  "Super Admin dashboard": "超級管理員儀表板",
  "Opened from the org menu in settings when the viewer is a Super Admin. Then create organizations, grant and revoke Super Admins, provision and manage users, and edit org membership. Delete and disable live on Manage user. SSO is not configurable here, and Instance Settings has no documentation link for the SSO environment variable. Email and auto-org on that page are read-only. The gate is ENABLE_SUPER_ADMIN, accepting true or 1. The OpenHands-Cloud E2E and the bug bash are still outside this UI.":
    "檢視者是超級管理員時，從設定裡的組織選單開啟。然後可以建立組織、授予和撤銷超級管理員、開通並管理使用者，以及編輯組織成員關係。刪除和停用在管理使用者裡。這裡不能配置單點登入，例項設定也沒有單點登入環境變數的文件連結。那一頁上的郵件和自動建組織是隻讀的。開關是 ENABLE_SUPER_ADMIN，接受 true 或 1。OpenHands Cloud 端到端測試和缺陷清掃仍在這個介面之外。",
  Dashboard: "儀表板",
  "Instance overview. A normal org member must not open this route.":
    "例項總覽。普通組織成員不能開啟這個路由。",
  "Gated by ENABLE_SUPER_ADMIN, accepting true or 1":
    "由 ENABLE_SUPER_ADMIN 把關，接受 true 或 1",
  "Instance overview with the next setup-guide step":
    "例項總覽，並帶出設定指南的下一步",
  "Conversation list and stop actions": "對話列表和停止操作",
  "Org menu pins Super Admin with a yellow shield":
    "組織選單用黃色盾牌釘住超級管理員",
  "Confirm ENABLE_SUPER_ADMIN accepts both true and 1, and a normal org member is denied. Review empty, error, and stop-conversation states more than layout. The shield belongs only on /super-admin.":
    "確認 ENABLE_SUPER_ADMIN 同時接受 true 和 1，並且普通組織成員會被拒絕。空狀態、錯誤狀態和停止對話比版面更值得看。盾牌只應出現在 /super-admin。",
  Organizations: "組織",
  "List, create, open, suspend, and resume organizations. Existing orgs default to active.":
    "列出、建立、開啟、暫停和恢復組織。已有組織預設是活躍的。",
  "Create, open, suspend, and resume organizations":
    "建立、開啟、暫停和恢復組織",
  "Suspend blocks usage and can be reversed": "暫停會擋住使用，並且可以恢復",
  "Opening an org you are not in asks you to join first":
    "開啟一個你不在其中的組織時，會先要求你加入",
  "Existing organizations default to active": "已有組織預設是活躍的",
  "Review migration 144. Existing orgs must default to active. Suspend has to block usage, resume has to undo it, and callers must not ignore org.status.":
    "看遷移 144。已有組織必須預設活躍。暫停必須擋住使用，恢復必須撤銷暫停，呼叫方不能忽略 org.status。",
  "Org status": "組織狀態",
  "Create organization": "建立組織",
  "Name, owner, and contact email for a new organization.":
    "新組織的名稱、所有者和聯絡郵箱。",
  "Opened from the org menu and from Super Admin organizations":
    "從組織選單和超級管理員的組織頁開啟",
  "Sets the name, owner, and contact email": "設定名稱、所有者和聯絡郵箱",
  "Confirm the owner and contact email are the values that get stored, and the org is created active.":
    "確認存下來的是所有者和聯絡郵箱，並且組織建立後是活躍的。",
  Membership: "成員關係",
  Users: "使用者",
  "Everyone on the instance. Provision a user and edit membership across organizations.":
    "例項上的所有人。開通使用者，並跨組織編輯成員關係。",
  "Provision a user into one or more organizations":
    "把使用者開通到一個或多個組織",
  "Role is chosen per organization": "每個組織單獨選擇角色",
  "Manage that person's access across organizations":
    "管理這個人在各個組織里的存取權限",
  "Password and API key are shown after provision":
    "開通之後會顯示密碼和 API 金鑰",
  "Confirm only a Super Admin can provision, the role is stored per org, and the returned API key is not logged. Cross-org writes go through the membership service.":
    "確認只有超級管理員可以開通，角色按組織儲存，返回的 API 金鑰不會被記入日誌。跨組織寫入要走成員關係服務。",
  Provisioning: "開通使用者",
  "Provision User": "開通使用者",
  "Creates a user in one or more organizations and can return a password and API key.":
    "在一個或多個組織里建立使用者，並可以返回密碼和 API 金鑰。",
  "Create a user in one or more organizations": "在一個或多個組織里建立使用者",
  "Can return a password and an API key": "可以返回密碼和 API 金鑰",
  "Confirm who can call provision, that each selected org stores its own role, and that a failed call does not show credentials.":
    "確認誰可以呼叫開通，每個選中的組織各自存角色，失敗的呼叫不會顯示憑據。",
  "Provision credentials": "開通憑據",
  "Shows the new password and LiteLLM API key in plain text. Confirm those values are not logged.":
    "用明文顯示新密碼和 LiteLLM API 金鑰。確認這些值不會被記入日誌。",
  "Shows the new password in plain text": "用明文顯示新密碼",
  "Shows the LiteLLM API key in plain text": "用明文顯示 LiteLLM API 金鑰",
  "Copy to clipboard": "複製到剪貼簿",
  "The LiteLLM key is plain text. Confirm it is not logged or written to localStorage, and that closing the dialog removes it from the page.":
    "LiteLLM 金鑰是明文。確認它不會被記入日誌或寫入 localStorage，並且關掉對話框後頁面上不再有它。",
  "Manage user": "管理使用者",
  "Membership and role for one person across organizations. Watch the last-owner rule.":
    "一個人在各個組織里的成員關係和角色。注意最後一位所有者的規則。",
  "Org access and role for one person": "一個人的組織存取權限和角色",
  "Add or change membership in specific organizations":
    "在指定組織里新增或更改成員關係",
  "Last-owner rule still applies": "最後一位所有者的規則仍然適用",
  "Review cross-org add, remove, and role changes. A Super Admin must not remove the last owner of an org.":
    "看跨組織的新增、移除和角色變更。超級管理員不能移除某個組織的最後一位所有者。",
  "Grant yourself access": "給自己授權",
  "A Super Admin picks a role and joins an organization before opening it.":
    "超級管理員先選一個角色並加入組織，然後才能開啟它。",
  "A Super Admin must join an organization before opening it":
    "超級管理員必須先加入組織，才能開啟它",
  "The role is chosen at join time": "加入時選擇角色",
  "Opening an org without membership must stay blocked until this completes, and the role written must be the one selected.":
    "沒有成員關係時開啟組織必須一直被攔住，直到這一步完成，並且寫入的角色必須是選中的那個。",
  "Super Admins": "超級管理員",
  "Who holds the instance role. Grant and revoke require manage_super_admins.":
    "誰擁有例項角色。授予和撤銷需要 manage_super_admins。",
  "Lists who holds the instance Super Admin role":
    "列出誰擁有例項超級管理員角色",
  "Grant the role by email": "按郵箱授予該角色",
  "Revoke requires manage_super_admins": "撤銷需要 manage_super_admins",
  "A normal org member must not reach this page":
    "普通組織成員不能進入這個頁面",
  "Grant and revoke must require manage_super_admins. Confirm this route cannot bypass the Super Admin access check, and an org admin cannot open it.":
    "授予和撤銷必須要求 manage_super_admins。確認這個路由不能繞過超級管理員訪問檢查，組織管理員也不能開啟它。",
  Access: "存取權限",
  "Grant Super Admin": "授予超級管理員",
  "Gives the instance Super Admin role to an email. A normal org member must not be able to do this.":
    "把例項超級管理員角色授給一個郵箱。普通組織成員不能做這件事。",
  "Grants the instance Super Admin role by email":
    "按郵箱授予例項超級管理員角色",
  "Requires manage_super_admins": "需要 manage_super_admins",
  "A caller without manage_super_admins must be rejected. Confirm granting does not also skip org membership checks for that user.":
    "沒有 manage_super_admins 的呼叫方必須被拒絕。確認授予角色時，不會同時跳過該使用者的組織成員檢查。",
  "Instance Settings": "例項設定",
  "Instance logo and email status. Check the permission-denied state, not the layout.":
    "例項標誌和郵件狀態。看的是無權限狀態，不是版面。",
  "Instance logo": "例項標誌",
  "Email delivery status": "郵件送達狀態",
  "Optional SAML setup step links here": "可選的 SAML 設定步驟連結到這裡",
  "Review the permission-denied and error states. Logo and email status must stay behind Super Admin access.":
    "看無權限和錯誤狀態。標誌和郵件狀態必須留在超級管理員存取權限後面。",
  "Settings chrome · this branch": "設定外觀 · 當前分支",
  "Neo theme on the screens these flows land in":
    "這些流程落到的畫面上的 neo 主題",
  "Not its own product ticket. Visual pass on the settings screens around the two sprints. Review layout and save behavior, not access or provisioning.":
    "這不是單獨的產品工單。對這兩次迭代周圍的設定畫面做外觀檢查。看版面和儲存行為，不看存取權限或開通使用者。",
  "Usage for the current organization.": "當前組織的用量。",
  "Usage dashboard restyled with neo theme tokens":
    "用量儀表板已換成 neo 主題 token",
  "Loading skeletons match the neo chrome": "載入骨架和 neo 外觀一致",
  "Visual regression on the usage widgets and skeletons. No access or billing logic changed on this page.":
    "看用量小部件和骨架有沒有外觀回退。這個頁面沒有改存取權限或賬單邏輯。",
  Budgets: "預算",
  "Spending limits and alerts for the organization.": "組織的支出上限和提醒。",
  "Budgets restyled with neo theme tokens": "預算已換成 neo 主題 token",
  "Visual regression on budget tabs and alerts. Confirm limits still save the same way.":
    "看預算標籤和提醒有沒有外觀回退。確認上限仍按原來的方式儲存。",
  Condenser: "Condenser",
  "How conversation history is condensed for the organization.":
    "組織如何壓縮對話歷史。",
  "Grouped under Language Model in the reordered settings nav":
    "在重新排序的設定導航裡，歸在語言模型下面",
  "Visual and nav-order check. Confirm condenser settings still save, and the ACP disable still applies if it did before.":
    "看外觀和導航順序。確認 condenser 設定仍能儲存，並且如果以前會停用 ACP，現在仍然會。",
  Verification: "驗證",
  "Checks that the organization LLM setup works.":
    "檢查組織的語言模型設定是否可用。",
  "Visual and nav-order check. Confirm verification still runs against the org LLM setup.":
    "看外觀和導航順序。確認驗證仍針對組織的語言模型設定執行。",
  "Billing & Credits": "賬單與積分",
  "Plan, credits, and payment for the organization.":
    "組織的方案、積分和付款。",
  "Billing restyled with neo theme tokens": "賬單已換成 neo 主題 token",
  "Visual pass. This is not the provisioned LiteLLM key. Confirm plan and credit actions still submit.":
    "做一遍外觀檢查。這不是開通時返回的 LiteLLM 金鑰。確認方案和積分操作仍會提交。",
  "Name and deletion for the current organization.": "當前組織的名稱和刪除。",
  "Create organization is available from the org menu":
    "可以從組織選單建立組織",
  "Rename and delete stay on this organization":
    "重新命名和刪除仍作用於這個組織",
  "Confirm Create organization in the org menu does not break org switching. Delete here is the current org, not Super Admin suspend.":
    "確認組織選單裡的建立組織不會弄壞組織切換。這裡的刪除是當前組織，不是超級管理員的暫停。",
  "Change organization name": "更改組織名稱",
  "Renames the current organization.": "重新命名當前組織。",
  "Renames the current organization": "重新命名當前組織",
  "Existing rename. Confirm the saved name is the field value and the org menu updates after save.":
    "這是原有的重新命名。確認儲存的名稱就是欄位裡的值，並且儲存後組織選單會更新。",
  "Delete organization": "刪除組織",
  "Confirms deleting the current organization.": "確認刪除當前組織。",
  "Confirms deletion of the current organization": "確認刪除當前組織",
  "Existing delete. This is not Super Admin suspend. Confirm it deletes only the current org and still requires the confirm step.":
    "這是原有的刪除。這不是超級管理員的暫停。確認它只刪除當前組織，並且仍需要確認這一步。",
  Agent: "代理",
  "Agent behavior settings for the organization.": "組織的代理行為設定。",
  "Visual pass on the neo chrome. Confirm agent settings still save with the existing form.":
    "對 neo 外觀做一遍檢查。確認代理設定仍用現有表單儲存。",
  "API Keys": "API 金鑰",
  "Keys the organization uses to call OpenHands.":
    "組織用來呼叫 OpenHands 的金鑰。",
  "Visual pass. Keys should still save immediately. This is separate from the LiteLLM key returned by provisioning.":
    "做一遍外觀檢查。金鑰仍應立即儲存。這和開通時返回的 LiteLLM 金鑰是分開的。",
  Secrets: "機密",
  "Named secrets available to conversations.": "對話可以使用的具名機密。",
  "Visual pass. Adding and deleting a secret should still hit the secrets API immediately, with values masked after save.":
    "做一遍外觀檢查。新增和刪除機密仍應立刻打到機密介面，儲存後值要被遮住。",
  Skills: "技能",
  "Organization skills the agent can load.": "代理可以載入的組織技能。",
  "Visual pass. Confirm adding and removing a skill still saves immediately.":
    "做一遍外觀檢查。確認新增和移除技能仍會立即儲存。",
  "OpenHands-Neo settings chrome, form controls, and toasts":
    "OpenHands-Neo 的設定外觀、表單控制元件和提示",
  "Settings nav brand reads Account": "設定導航的品牌文案是帳戶",
  Usage: "用量",
};

export function catalogText(language: string, text: string): string {
  if (language === "zh-CN") {
    return ZH_CN[text] ?? text;
  }
  if (language === "zh-TW") {
    return ZH_TW[text] ?? text;
  }
  return text;
}
