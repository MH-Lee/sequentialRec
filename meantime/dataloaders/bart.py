from .base import AbstractDataloader

import torch
import torch.utils.data as data_utils
import pdb
import copy

class BertDataloader(AbstractDataloader):
    @classmethod
    def code(cls):
        return 'bart'

    def _get_dataset(self, mode):
        if mode == 'train':
            return self._get_train_dataset()
        elif mode == 'val':
            return self._get_eval_dataset('val')
        else:
            return self._get_eval_dataset('test')

    def _get_train_dataset(self):
        train_ranges = self.train_targets
        dataset = BertTrainDataset(self.args, self.dataset, self.train_negative_samples, self.rng, train_ranges)
        return dataset

    def _get_eval_dataset(self, mode):
        positions = self.validation_targets if mode=='val' else self.test_targets
        dataset = BertEvalDataset(self.args, self.dataset, self.test_negative_samples, positions)
        return dataset


class BertTrainDataset(data_utils.Dataset):
    def __init__(self, args, dataset, negative_samples, rng, train_ranges):
        self.args = args
        self.user2dict = dataset['user2dict']
        self.users = sorted(self.user2dict.keys())
        self.train_window = args.train_window
        self.max_len = args.max_len
        self.mask_prob = args.mask_prob
        self.special_tokens = dataset['special_tokens']
        self.num_users = len(dataset['umap'])
        self.num_items = len(dataset['smap'])
        self.rng = rng
        self.train_ranges = train_ranges

        self.index2user_and_offsets = self.populate_indices()

        self.output_timestamps = args.dataloader_output_timestamp
        self.output_days = args.dataloader_output_days
        self.output_user = args.dataloader_output_user

        self.negative_samples = negative_samples
        # pdb.set_trace()

    def get_rng_state(self):
        return self.rng.getstate()

    def set_rng_state(self, state):
        return self.rng.setstate(state)

    def populate_indices(self):
        index2user_and_offsets = {}
        i = 0
        T = self.max_len
        W = self.train_window

        # offset is exclusive
        for user, pos in self.train_ranges:
            if W is None or W == 0:
                offsets = [pos]
            else:
                offsets = list(range(pos, T-1, -W))  # pos ~ T, 摘除多个有效的行为序列; 
                if len(offsets) == 0:
                    offsets = [pos]
            for offset in offsets:
                index2user_and_offsets[i] = (user, offset)
                i += 1
        return index2user_and_offsets #一共是8700条数据;

    def __len__(self):
        return len(self.index2user_and_offsets)

    def __getitem__(self, index):
        user, offset = self.index2user_and_offsets[index]
        seq = self.user2dict[user]['items']
        # beg = max(0, offset-self.max_len-1) #最后一个作为目标;
        end = offset  # exclude offset (meant to be)
        # seq = seq[beg:end] #由于在获取数据时, 设置offset为sequence-2, 因此不能包含索引为n-2的元素;
        d = {}
        # #decoder input
        # tokens = seq[:-1]
        # padding_len = self.max_len - len(tokens)
        # # labels = seq[1:]
        # tokens = [0] * padding_len + tokens
        # # labels = [0] * padding_len + labels
        # d['tokens'] = torch.LongTensor(tokens)
        # d['labels'] = torch.LongTensor(labels)
        
        #encoder input
        beg_encoder = max(0, offset-self.max_len)
        seq = seq[beg_encoder:end]
        tokens_encoder = []
        labels = []
        for index, s in enumerate(seq):
            prob = self.rng.random()
            if prob < self.mask_prob:
                prob /= self.mask_prob
                # (1) 只采用mask token方法;
                # tokens_encoder.append(self.special_tokens.mask)
                # (2) 类似于bert, mask, replace and keep same 同时存在;
                if prob < 0.8:
                    tokens_encoder.append(self.special_tokens.mask)
                elif prob < 0.9:
                    tokens_encoder.append(self.rng.randint(1, self.num_items))
                else:
                    tokens_encoder.append(s)
                labels.append(s)
            else:
                tokens_encoder.append(s)
                labels.append(0)

        tokens_encoder = tokens_encoder[-self.max_len:]
        labels = labels[-self.max_len:]

        padding_len = self.max_len - len(tokens_encoder)
        valid_len = len(tokens_encoder)

        tokens_encoder = [0] * padding_len + tokens_encoder
        labels = [0] * padding_len + labels

        d['tokens_pair'] = torch.LongTensor(tokens_encoder)
        d['labels'] = torch.LongTensor(labels)

        tokens = seq[:-1]
        padding_len = self.max_len - len(tokens)
        # labels = seq[1:]
        tokens = [0] * padding_len + tokens
        # labels = [0] * padding_len + labels
        d['tokens'] = torch.LongTensor(tokens)
        
        # pdb.set_trace()
        return d


class BertEvalDataset(data_utils.Dataset):
    def __init__(self, args, dataset, negative_samples, positions):
        self.user2dict = dataset['user2dict']
        self.positions = positions
        self.max_len = args.max_len
        self.num_items = len(dataset['smap'])
        self.special_tokens = dataset['special_tokens']
        self.negative_samples = negative_samples

        self.output_timestamps = args.dataloader_output_timestamp
        self.output_days = args.dataloader_output_days
        self.output_user = args.dataloader_output_user

    def __len__(self):
        return len(self.positions)

    def __getitem__(self, index):
        user, pos = self.positions[index]
        seq = self.user2dict[user]['items']

        beg = max(0, pos + 1 - self.max_len)
        end = pos + 1 #读取数据时, valid设置为n-2, test设置为n-1, 其中n是句子长度, 因此此处需要add 1;
        seq = seq[beg:end]

        negs = self.negative_samples[user]
        answer = [seq[-1]]
        candidates = answer + negs
        labels = [1] * len(answer) + [0] * len(negs)

        seq[-1] = self.special_tokens.mask
        padding_len = self.max_len - len(seq)
        seq = [0] * padding_len + seq

        tokens = torch.LongTensor(seq) #encoder input;
        candidates = torch.LongTensor(candidates)
        labels = torch.LongTensor(labels)
        d = {'tokens_pair':tokens, 'candidates':candidates, 'labels':labels}

        #input for decoder
        tokens_decoder = seq[:-1]
        padding_len = self.max_len - len(tokens_decoder)
        # labels = seq[1:]
        tokens_decoder = [0] * padding_len + tokens_decoder
        # labels = [0] * padding_len + labels
        d['tokens'] = torch.LongTensor(tokens_decoder)
        #input for encoder;
        # beg = max(0, pos + 1 - self.max_len)
        # end = pos + 1 #读取数据时, valid设置为n-2, test设置为n-1, 其中n是句子长度, 因此此处需要add 1;
        # seq_encoder = copy.deepcopy(seq[beg:end])

        # seq_encoder[-1] = self.special_tokens.mask
        # padding_len = self.max_len - len(seq_encoder)
        # seq_encoder = [0] * padding_len + seq_encoder

        # seq_encoder_tokens = torch.LongTensor(seq_encoder)
        # candidates = torch.LongTensor(candidates)
        # labels = torch.LongTensor(labels)
        # d = {'tokens_pair':seq_encoder_tokens}

        # if self.output_user:
        #     d['users'] = torch.LongTensor([user])

        #input for decoder;
        # max_len = self.max_len
        # beg = max(0, pos - max_len) #此处与bert模型不同; bert模型将目标位置设置为mask, 而sasrec模型则忽略目标位置;
        # end = pos
        # answer = [seq[pos]]
        # seq = seq[beg:end]

        # negs = self.negative_samples[user]
        # candidates = answer + negs
        # labels = [1] * len(answer) + [0] * len(negs)

        # # IMPORTANT : no [MASK]s for sas
        # # so the next line is commented
        # # seq[-1] = self.special_tokens.mask
        # padding_len = max_len - len(seq)
        # seq = [0] * padding_len + seq

        # tokens = torch.LongTensor(seq)
        # candidates = torch.LongTensor(candidates)
        # labels = torch.LongTensor(labels)
        # d = {'tokens_pair':seq_encoder_tokens, 'tokens':tokens, 'candidates':candidates, 'labels':labels}
        # pdb.set_trace()
        return d
