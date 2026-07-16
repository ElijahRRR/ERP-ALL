# 生产前端镜像（INFRA-0716）：构建产物 + nginx 静态伺服。
#
# 动因：生产此前跑 dev-profile vite（bind-mount + HMR），git 检出/容器状态变化时
# 浏览器会拿到新旧混杂的 stale 模块——HF-0716①「无订单页」与 FE-0716「翻页菜单
# 消失」两起生产事故同源。构建产物 + 内容寻址资产 + index.html 不缓存彻底根治。
# 部署 = 重建镜像（compose up -d --build frontend），不再有进程内缓存态。

FROM node:22-alpine AS build
WORKDIR /app
# corepack 按 package.json 的 packageManager 钉死 pnpm 版本（compose dev 同款教训）
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
