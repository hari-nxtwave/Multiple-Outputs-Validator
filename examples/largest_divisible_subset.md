# Largest Divisible Subset

Given a set of distinct positive integers `arr`, return the **largest subset**
`answer` such that every pair `(answer[i], answer[j])` of elements satisfies
`answer[i] % answer[j] == 0` or `answer[j] % answer[i] == 0`.

If there are multiple subsets of the maximum size, you may **return any one of
them**. (So two correct submissions can return genuinely different subsets.)

Implement:

```java
class Solution {
    public List<Integer> findLargestChain(int[] arr) { ... }
}
```

Input format: the first line is `n`, followed by `n` integers.
Output: print the elements of the subset separated by spaces.

```java
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        int[] arr = new int[n];
        for (int i = 0; i < n; i++) arr[i] = sc.nextInt();
        Solution sol = new Solution();
        List<Integer> result = sol.findLargestChain(arr);
        for (int num : result) System.out.print(num + " ");
    }
}
```
