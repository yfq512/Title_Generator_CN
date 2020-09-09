#! -*- coding: utf-8 -*-

from __future__ import print_function

import glob
import os, sys
import numpy as np
import tensorflow as tf
sys.path.append(sys.path[0]+'/bert4keras/')
from bert4keras.backend import keras, K
from bert4keras.layers import Loss
from bert4keras.models import build_transformer_model
from bert4keras.tokenizers import Tokenizer, load_vocab
from bert4keras.optimizers import Adam
from bert4keras.snippets import sequence_padding, open
from bert4keras.snippets import DataGenerator, AutoRegressiveDecoder
from keras.models import Model

# 基本参数
maxlen = 320
title_ml = 32
content_ml = maxlen - title_ml
batch_size = 16
steps_per_epoch = 1000
epochs = 1000

# bert配置
config_path = sys.path[0] + '/chinese_L-12_H-768_A-12/bert_config.json'
checkpoint_path = sys.path[0] + '/chinese_L-12_H-768_A-12/bert_model.ckpt'
dict_path = sys.path[0] + '/chinese_L-12_H-768_A-12/vocab.txt'


# 训练样本。THUCNews数据集，每个样本保存为一个txt。
txts = glob.glob('/data/ywj/THUCNews/*/*.txt')

# 加载并精简词表，建立分词器
token_dict, keep_tokens = load_vocab(
    dict_path=dict_path,
    simplified=True,
    startswith=['[PAD]', '[UNK]', '[CLS]', '[SEP]'],
)
tokenizer = Tokenizer(token_dict, do_lower_case=True)


class data_generator(DataGenerator):
    """数据生成器
    """
    def __iter__(self, random=False):
        batch_token_ids, batch_segment_ids = [], []
        for is_end, txt in self.sample(random):
            text = open(txt, encoding='utf-8').read()
            text = text.split('\n')
            if len(text) > 1:
                title = text[0]
                content = '\n'.join(text[1:])

                content_token_ids,content_segment_ids = tokenizer.encode(
                    content, maxlen=content_ml)
                content_segment_ids = [0]*content_ml

                l = len(content_token_ids)
                if l < content_ml:
                    del content_token_ids[-1]
                    for i in range(content_ml - l):
                        content_token_ids.append(0)
                    content_token_ids.append(3)

                title_token_ids,title_segment_ids = tokenizer.encode(
                    title, maxlen=title_ml)
                del title_token_ids[0]
                l = len(title_token_ids)
                title_segment_ids = [1]*l
                if l < title_ml:
                    for i in range(title_ml - l):
                        title_token_ids.append(0)
                        title_segment_ids.append(0)
                token_ids = content_token_ids + title_token_ids
                segment_ids = content_segment_ids + title_segment_ids
                batch_token_ids.append(token_ids)
                batch_segment_ids.append(segment_ids)
            if len(batch_token_ids) == self.batch_size or is_end:
                
                batch_token_ids = sequence_padding(batch_token_ids,length=320)
                batch_segment_ids = sequence_padding(batch_segment_ids,length=320)
                yield [batch_token_ids, batch_segment_ids], None
                batch_token_ids, batch_segment_ids = [], []


class CrossEntropy(Loss):
    """交叉熵作为loss，并mask掉输入部分
    """
    def compute_loss(self, inputs, mask=None):
        y_true, y_mask, y_pred = inputs
        y_true = y_true[:, 1:]  # 目标token_ids
        y_mask = y_mask[:, 1:]  # segment_ids，刚好指示了要预测的部分
        y_pred = y_pred[:, :-1]  # 预测序列，错开一位
        loss = K.sparse_categorical_crossentropy(y_true, y_pred)
        loss = K.sum(loss * y_mask) / K.sum(y_mask)
        return loss


model = build_transformer_model(
    config_path,
    checkpoint_path,
    application='unilm',
    keep_tokens=keep_tokens,  # 只保留keep_tokens中的字，精简原字表
)

output = CrossEntropy(2)(model.inputs + model.outputs)

model = Model(model.inputs, output)
model.compile(optimizer=Adam(1e-5))
model.summary()


class AutoTitle(AutoRegressiveDecoder):
    """seq2seq解码器
    """
    @AutoRegressiveDecoder.wraps(default_rtype='probas')
    def predict(self, inputs, output_ids, states):
        token_ids, segment_ids = inputs
        token_ids = np.concatenate([token_ids, output_ids], 1)
        segment_ids = np.concatenate([segment_ids, np.ones_like(output_ids)], 1)
        pdt = model.predict([token_ids, segment_ids])
        #input: [ array([[2,…]]), array([[0,…]]) ]    list, numpy.ndarray
        #model.predict.shape: (1, len(array), 13584)   numpy.ndarray
        
        nxt_wd_probs = pdt[:, -1]
        #nxt_wd_probs.shape: (1, 13584)  numpy.ndarray
        return nxt_wd_probs

    def greedy(self, inputs, states=None, min_ends=1):
        inputs = [np.array([i]) for i in inputs]
        output_ids = self.first_output_ids
        for step in range(self.maxlen): #暂 最长32字标题
            scores, states = self.predict(inputs, output_ids, states, 'logits')
            a = np.argmax(scores)
            b = np.array([[a]])
            output_ids = np.concatenate((output_ids, b), axis=1)
            if a == 3:
                return output_ids[0]
        output_ids = np.concatenate((output_ids, np.array([[3]])), axis=1)
        return output_ids[0]

    def generate(self, text, topk=1):
        max_c_len = maxlen- self.maxlen

        token_ids, segment_ids = tokenizer.encode(text, maxlen=max_c_len)
        segment_ids = [0]*max_c_len
        l = len(token_ids)
        if l < max_c_len:
            del token_ids[-1]
            for i in range(max_c_len - l):
                token_ids.append(0)
            token_ids.append(3)
        output_ids = self.greedy([token_ids, segment_ids])

        return tokenizer.decode(output_ids)

autotitle = AutoTitle(start_id=None, end_id=tokenizer._token_end_id, maxlen=32)


def just_show():
    s1 = u'夏天来临，皮肤在强烈紫外线的照射下，晒伤不可避免，因此，晒后及时修复显得尤为重要，否则可能会造成长期伤害。专家表示，选择晒后护肤品要慎重，芦荟凝胶是最安全，有效的一种选择，晒伤严重者，还请及 时 就医 。'
    s2 = u'8月28日，网络爆料称，华住集团旗下连锁酒店用户数据疑似发生泄露。从卖家发布的内容看，数据包含华住旗下汉庭、禧玥、桔子、宜必思等10余个品牌酒店的住客信息。泄露的信息包括华住官网注册资料、酒店入住登记的身份信息及酒店开房记录，住客姓名、手机号、邮箱、身份证号、登录账号密码等。卖家对这个约5亿条数据打包出售。第三方安全平台威胁猎人对信息出售者提供的三万条数据进行验证，认为数据真实性非常高。当天下午 ，华 住集 团发声明称，已在内部迅速开展核查，并第一时间报警。当晚，上海警方消息称，接到华住集团报案，警方已经介入调查。'
    s3 = u'本报讯 上海天然橡胶期价周三再创年内新高，主力合约突破21000元/吨重要关口。分析师指出，由于橡胶现货需求强劲，但供应却因主产国降雨天气而紧俏。同时国内有望出台新汽车刺激方案，沪胶后市有望延续强势。经过两个交易日的强势调整后，昨日上海天然橡胶期货价格再度大幅上扬，在收盘前1小时，大量场外资金涌入，主力1003合约强劲飙升很快升穿21000 元/吨整数关口，终盘报收于21,400元/吨，上涨2.27%，较前一日结算价上涨475元/吨，成交量为736,816手，持仓量为225,046 手。当日整体市场增仓3.4万余手。从盘后交易所持仓来看，两大主力多头金鹏期货和成都倍特期货略微增几百手，继续保持多头前两名位置，而主力多头新湖期货增仓3344手，值得注意的是，永安期货昨日空翻多，增加多仓1837手，其多头持仓增加至7021手，而净持仓增加至1813 手；空头两大主力则继续大幅增仓，其中浙江大地增仓2522手至17294手，银河期货增仓1075手至7086手。与此同时，东京商品交易所橡胶期货也强势上扬，基准4月合约再创13个月新高。截止北京时间昨日下午16点46分报241.5日元/公斤，较前日收盘涨3.2日元。金鹏期货北京海鹰路营业部总经理陈旭指出，近期沪胶受资金推动持续升创年内新高，而橡胶现货需求强劲，但供应却因主产国降雨天气而紧张。同时国内有望出台新汽车刺激方案，因此沪胶后市有望延续强势。泰国橡胶协会秘书长Prapas Euanontat16日表示，因暴雨中断生产，2009年该国橡胶产量可能下降约10%，为270万吨至280万吨。另据日本橡胶贸易协会最新数据，截至11月10日，该国天然橡胶库存较10月31日时的库存量下滑3.4%，至3902吨，创纪录新低。据国家统计局公布的最新数据显示，9月份国内轮胎产量较2008年同期增长27%至5,810万条，较8月份增长10%。1-9月份轮胎总产量增长13%至4.814亿条。这表明在特保案发生前，中国的轮胎出口已经产生巨大的需求，特保案生效也可能不会太大地削弱市场规模。陈旭表示，国家仍将汽车行业作为拉动经济增长的重要手段，这可能会在近期即将召开的经济会议中得到体现，中国扩大内需的方针正刺激天胶等原材料消费，引起贸易商囤积库存。不过，也有分析师表示，国内橡胶库存高企，逼近13万吨，且随着主产国降雨天气结束供应将持续增加，因此沪胶后市上行空间有限。'
    s4 = u'东方网记者傅文婧9月1日报道：伴随着风和日丽的好天气，9月1日上午，沪上中小学迎来了新学期。早上8点，东方网记者在杨浦区世界小学看到，学生们按照规定返校时间在校门口排队进校，洗手、测温有条不紊，全校785名学生在30分钟内有序完成进校。“因为疫情的原因，这两个月的暑假我都不能到人多的地方去，所以我特别想念学校的生活。”五年级的孙展鹏同学说，“今天终于开学了，又能见到老师和同学们了，所以我特别开心，同时也下定决心一定要在新学期中取得优异的成绩，并帮助学习有困难的同学，让大家共同进步。”一位五年级学生家长也告诉记者，希望孩子在“小升初”前的这一学年里能够好好学习，为升入初中做好准备。任教三年级的施佳谊老师表示，上个学期受到疫情影响，和小朋友们的相处只有一个月左右，很快就放假，时间“太短”了。新学期能够如期开学，能够在校园里面看见小朋友们的笑脸，她感到非常高兴，“就感觉向往的校园生活又回来了！”施老师透露，在开学之前，学校已经召开了家长会介绍各项防疫工作，提醒小朋友们需要注意哪些地方。对于新学期，施老师的愿望很简单，“希望小朋友能够开开心心、平平安安地来上学，度过开心的每一天。” 世界小学德育中心主任李薇老师向记者介绍，为积极响应节约粮食、珍惜食物的号召，新学期里，学校将以多种形式倡导“光盘行动”。“我们会开展每周‘节粮班’的评比，颁发‘米宝宝’奖牌。通过评比促进学生爱粮节粮，进一步培养厉行节约的好习惯。”此外，根据近日教育部联合红十字会印发的《进一步加强学校红十字工作》相关文件，学校会针对青少年生理和心理特点，积极开展红十字应急救护培训，“通过专题教育课，在高年级学生中开展心肺复苏这样的教学，提高学生的健康素养。” 采访中，记者还注意到学校的教室、办公室、卫生间等各处门口都贴有一个二维码。“这是我们学校自创的防消专用平台。”李薇老师介绍，每个地方在完成每日例行的防疫消毒工作后，通过扫描二维码就能在平台上“打卡”，随后会有检查人员对现场的卫生情况进行检查，再在平台上完成审核步骤，确保严格执行防消工作。“因为疫情还没有结束，所以防控是常态。”李薇老师还表示，“我们也会继续引导学生，养成良好的卫生习惯。”'
    
    for s in [s1, s2, s3, s4]:
        print(u'生成标题:', autotitle.generate(s))
    print()


class Evaluator(keras.callbacks.Callback):
    def __init__(self):
        self.lowest = 1e10

    def on_epoch_end(self, epoch, logs=None):
        # 保存最优
        if logs['loss'] <= self.lowest:
            self.lowest = logs['loss']
            model.save_weights(sys.path[0] +'/model/best_model.weights')
            tf.saved_model.save(model, sys.path[0] +'/savedmodel/')
        # 演示效果
        just_show()


if __name__ == '__main__':

    evaluator = Evaluator()
    train_generator = data_generator(txts, batch_size)

    model.fit_generator(
        train_generator.forfit(),
        steps_per_epoch=steps_per_epoch,
        epochs=epochs,
        callbacks=[evaluator]
    )