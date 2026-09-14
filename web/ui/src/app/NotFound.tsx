import { Button, Result } from 'antd'
import { Link } from 'react-router'

import { PageTitle } from './PageTitle'

/**
 * 404。
 *
 * 两条要求（本会话 §10）：一句**业务能看懂**的说明，一个出口。
 *
 * 不写「路由未匹配」「Not Found」这类工程语言 —— 她不是来调试路由的，
 * 她是点了一个飞书卡片里的旧链接。说清楚这件事，然后把她送回队列。
 */
export function NotFound() {
  return (
    <>
      <PageTitle />
      <Result
        status="404"
        title="这个地址在审校台里没有对应的界面"
        subTitle="链接可能来自更早的版本，或者这一篇已经不在可打开的范围内。回到审校队列重新挑一篇。"
        extra={
          <Link to="/review">
            <Button type="primary">返回审校队列</Button>
          </Link>
        }
      />
    </>
  )
}
